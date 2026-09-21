from __future__ import annotations

import fnmatch
import json
import os
import stat
import time
from pathlib import Path

from .base import MAX_FILE_BYTES, MAX_OUTPUT_BYTES, Tool, ToolContext

EXCLUDED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
            ".deepblue", ".test-tmp", ".cache", ".pytest_cache", "build", "dist"}
MAX_ENTRIES = 20_000
MAX_SEARCH_SECONDS = 5


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def matches(path: str, pattern: str) -> bool:
    target = path if "/" in pattern else path.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(target, pattern) or (pattern.startswith("**/") and matches(path, pattern[3:]))


def files(root: Path, include_hidden: bool, info: dict):
    if is_link(root):
        raise ValueError("搜索不跟随符号链接或 junction。")
    if root.is_file():
        yield root, root.name
        return
    if not root.is_dir():
        raise ValueError("搜索路径不是文件或目录。")
    deadline = time.monotonic() + MAX_SEARCH_SECONDS
    entries = 0

    def inaccessible(error):
        info["skipped"] += 1

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=inaccessible):
        kept = []
        for name, is_dir in [(name, True) for name in sorted(dirs)] + [(name, False) for name in sorted(names)]:
            entries += 1
            if entries > MAX_ENTRIES or time.monotonic() > deadline:
                info.update(truncated=True, reason="达到扫描数量或时间上限，请缩小 path 范围。")
                return
            if name in EXCLUDED or (not include_hidden and name.startswith(".")):
                continue
            path = Path(directory) / name
            try:
                if is_link(path):
                    info["skipped"] += 1
                elif is_dir:
                    kept.append(name)
                elif path.is_file():
                    yield path, path.relative_to(root).as_posix()
            except OSError:
                info["skipped"] += 1
        dirs[:] = kept


def search(context: ToolContext, args: dict, content: bool) -> dict:
    # Do not resolve symlinks before the walker has had a chance to reject them.
    raw = Path(args.get("path", ".")).expanduser()
    root = raw if raw.is_absolute() else context.cwd / raw
    pattern = args["pattern"]
    limit = args.get("limit", 100)
    info = {"ok": True, "root": str(root.absolute()), "truncated": False, "skipped": 0}
    results = []
    size = 0

    def add(item):
        nonlocal size
        item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
        if len(results) >= limit or size + item_size > MAX_OUTPUT_BYTES:
            info.update(truncated=True, reason="达到返回条数或字节上限，请缩小搜索范围。")
            return False
        results.append(item)
        size += item_size
        return True

    needle = pattern.casefold() if args.get("ignore_case", False) else pattern
    for path, relative in files(root, args.get("include_hidden", False), info):
        if not content:
            if matches(relative, pattern) and not add(relative):
                break
            continue
        if not matches(relative, args.get("glob", "*")):
            continue
        try:
            with path.open("rb") as file:
                raw_content = file.read(MAX_FILE_BYTES + 1)
            if len(raw_content) > MAX_FILE_BYTES or b"\0" in raw_content:
                info["skipped"] += 1
                continue
            text = raw_content.decode("utf-8-sig")
        except (OSError, UnicodeError):
            info["skipped"] += 1
            continue
        for number, line in enumerate(text.splitlines(), 1):
            haystack = line.casefold() if args.get("ignore_case", False) else line
            if needle in haystack:
                # Keep bounded text around the match, even for minified lines.
                position = haystack.find(needle)
                if args.get("ignore_case", False):
                    folded_offset = 0
                    for original_position, character in enumerate(line):
                        folded_offset += len(character.casefold())
                        if folded_offset > position:
                            position = original_position
                            break
                offset = max(0, position - 150)
                excerpt = line[offset:offset + 500]
                if not add({"path": relative, "line": number, "text": excerpt,
                            "line_truncated": len(excerpt) < len(line)}):
                    info["matches"] = results
                    return info
    info["matches" if content else "files"] = results
    return info


COMMON = {"path": {"type": "string", "minLength": 1},
          "limit": {"type": "integer", "minimum": 1, "maximum": 500},
          "include_hidden": {"type": "boolean"}}
FIND = Tool("find", "按文件名或相对路径 glob 搜索，如 *.py、src/*.py。默认跳过隐藏和常见依赖目录，不跟随链接。返回路径，truncated=true 时缩小范围。", {
    **COMMON, "pattern": {"type": "string", "minLength": 1},
}, ["pattern"], lambda context, args: search(context, args, False))
GREP = Tool("grep", "在 UTF-8 文件中按字面文本搜索（不是正则），返回路径、行号与片段。可用 glob 限定文件，ignore_case 忽略大小写。跳过二进制和超大文件。", {
    **COMMON, "pattern": {"type": "string", "minLength": 1},
    "glob": {"type": "string", "minLength": 1}, "ignore_case": {"type": "boolean"},
}, ["pattern"], lambda context, args: search(context, args, True))
