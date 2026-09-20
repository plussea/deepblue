from .base import MAX_OUTPUT_BYTES, Tool, ToolContext


def read(context: ToolContext, args: dict) -> dict:
    path = context.path(args["path"])
    offset, limit = args.get("offset", 1), args.get("limit", 300)
    lines = []
    size = 0
    truncated = False
    # Read bounded binary lines so a minified / binary file cannot exhaust RAM.
    with path.open("rb") as file:
        index = 0
        while True:
            raw = file.readline(MAX_OUTPUT_BYTES + 1)
            if not raw:
                break
            index += 1
            if len(raw) > MAX_OUTPUT_BYTES:
                raise ValueError(f"第 {index} 行过长；请用 shell 提取所需片段。")
            if index < offset:
                continue
            if b"\0" in raw:
                raise ValueError("read 仅支持文本文件。")
            text = raw.decode("utf-8-sig" if index == 1 else "utf-8")
            rendered = f"{index}: {text.rstrip(chr(10)).rstrip(chr(13))}\n"
            if len(lines) >= limit or size + len(rendered.encode("utf-8")) > MAX_OUTPUT_BYTES:
                if not lines:
                    raise ValueError(f"第 {index} 行过长；请用 shell 提取所需片段。")
                truncated = True
                break
            size += len(rendered.encode("utf-8"))
            lines.append(rendered)
    return {"ok": True, "path": str(path), "content": "".join(lines),
            "truncated": truncated, "next_offset": offset + len(lines) if truncated else None}


TOOL = Tool("read", "按行读取 UTF-8 文本，返回行号。输出截断时用 next_offset 继续读取。", {
    "path": {"type": "string", "minLength": 1},
    "offset": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
}, ["path"], read)
