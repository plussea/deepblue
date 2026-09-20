import os
import tempfile
from pathlib import Path

from .base import MAX_FILE_BYTES, Tool, ToolContext


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".deepblue-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write(context: ToolContext, args: dict) -> dict:
    path = context.path(args["path"])
    data = args["content"].encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("单次写入不能超过 4 MiB。")
    atomic_write(path, data)
    return {"ok": True, "path": str(path), "bytes_written": len(data)}


TOOL = Tool("write", "创建或完整覆盖 UTF-8 文件。修改已有文件前先 read，局部修改优先 edit。", {
    "path": {"type": "string", "minLength": 1}, "content": {"type": "string"},
}, ["path", "content"], write)
