import difflib

from .base import MAX_FILE_BYTES, Tool, ToolContext, bounded
from .write import atomic_write


def edit(context: ToolContext, args: dict) -> dict:
    path = context.path(args["path"])
    with path.open("rb") as file:
        raw = file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("edit 仅支持不超过 4 MiB 的文件。")
    bom = raw.startswith(b"\xef\xbb\xbf")
    original = raw.decode("utf-8-sig")
    if "\0" in original:
        raise ValueError("edit 仅支持文本文件。")
    normalized = original.replace("\r\n", "\n")
    old = args["old_text"].replace("\r\n", "\n")
    new = args["new_text"].replace("\r\n", "\n")
    # Count overlapping occurrences too: replacing 'aa' in 'aaa' is ambiguous.
    first = normalized.find(old)
    if first < 0:
        raise ValueError("未找到 old_text，请重新读取文件。")
    if normalized.find(old, first + 1) >= 0:
        raise ValueError("old_text 匹配多处，请提供更多上下文。")
    if old == new:
        raise ValueError("新旧文本相同，没有修改。")
    # Map the normalized match back to original offsets, preserving untouched
    # bytes even in a file with mixed CRLF and LF line endings.
    offsets = []
    i = 0
    while i < len(original):
        offsets.append(i)
        i += 2 if original[i:i + 2] == "\r\n" else 1
    offsets.append(len(original))
    newline = "\r\n" if "\r\n" in original else "\n"
    updated = original[:offsets[first]] + new.replace("\n", newline) + original[offsets[first + len(old)]:]
    data = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("修改后文件超过 4 MiB。")
    # Detect common external edits between read and commit; this is not a lock
    # against other editors and does not promise transactional concurrency.
    if path.read_bytes() != raw:
        raise ValueError("文件被其他进程修改，请重新读取后编辑。")
    atomic_write(path, data)
    diff = "".join(difflib.unified_diff(normalized.splitlines(keepends=True),
                   updated.replace("\r\n", "\n").splitlines(keepends=True),
                   fromfile=str(path), tofile=str(path)))
    return {"ok": True, "path": str(path), "diff": bounded(diff)}


TOOL = Tool("edit", "对文件做一次唯一精确文本替换，保留 BOM 和换行，返回 diff。", {
    "path": {"type": "string", "minLength": 1},
    "old_text": {"type": "string", "minLength": 1},
    "new_text": {"type": "string"},
}, ["path", "old_text", "new_text"], edit)
