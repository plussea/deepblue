"""Read-only reconciliation; never replay a command or act on a historical PID."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_state(path: Path) -> dict:
    try:
        if not path.exists():
            return {"exists": False}
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            return {"unknown": "非普通文件或超过 8 MiB"}
        before = path.stat()
        with path.open("rb") as file:
            data = file.read(8 * 1024 * 1024 + 1)
        after = path.stat()
        if len(data) > 8 * 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return {"unknown": "读取期间文件变化"}
        return {"exists": True, "sha256": hashlib.sha256(data).hexdigest()}
    except OSError as exc:
        return {"unknown": type(exc).__name__}


def recovery_report(session) -> dict:
    pending, changed, confirmed = [], [], 0
    latest = {}
    for operation in session.operations.values():
        if operation.get("phase") == "finished":
            confirmed += 1
            if operation.get("path") and operation.get("after"):
                latest[operation["path"]] = operation["after"]
            continue
        item = {key: operation.get(key) for key in ("operation_id", "call_id", "name", "path", "log_path", "pid")}
        item["status"] = "unknown"
        if operation.get("path"):
            current = file_state(Path(operation["path"]))
            item["current"] = current
            if "unknown" not in current and current == operation.get("before"):
                item["status"] = "unchanged"
            elif current.get("sha256") and current["sha256"] == operation.get("expected_sha256"):
                item["status"] = "matches_intended_content"
            else:
                item["status"] = "changed_or_unknown"
        pending.append(item)
    for path, previous in latest.items():
        current = file_state(Path(path))
        if current != previous or "unknown" in current:
            changed.append({"path": path, "previous": previous, "current": current})
    return {"confirmed_operations": confirmed, "unfinished_operations": pending,
            "changed_files": changed, "action": "只读核对；未知操作不会重放。变化文件须重新读取；Shell 结果需人工或后续只读检查确认。"}


def execute_recorded(session, tools, call: dict) -> dict:
    import uuid
    import time
    started = time.monotonic()
    name, arguments = call["function"]["name"], call["function"]["arguments"]
    record = {"operation_id": uuid.uuid4().hex, "call_id": call["id"], "name": name,
              "phase": "started", "message_index": len(session.messages)}
    try:
        args = json.loads(arguments)
        if name in {"read", "write", "edit"} and isinstance(args.get("path"), str):
            path = tools.context.path(args["path"])
            record.update(path=str(path), before=file_state(path))
            if name == "write" and isinstance(args.get("content"), str):
                record["expected_sha256"] = hashlib.sha256(args["content"].encode("utf-8")).hexdigest()
    except (ValueError, TypeError, AttributeError):
        pass  # Tool validation returns the actual parameter error.
    session.record_operation(record)
    previous = None
    if record.get("path"):
        for item in session.operations.values():
            if item.get("phase") == "finished" and item.get("path") == record["path"] and item.get("after"):
                previous = item["after"]
    stale = (name in {"write", "edit"} and previous is not None and
             (record.get("before") != previous or "unknown" in previous))
    old_callback = tools.context.on_process
    tools.context.on_process = lambda info: session.record_operation({**record, **info, "phase": "started"})
    try:
        result = ({"ok": False, "error": "文件自上次观察后发生变化或无法核对。请先 read 最新内容，再决定是否修改。"}
                  if stale else tools.execute(name, arguments))
    finally:
        tools.context.on_process = old_callback
    finished = {**session.operations[record["operation_id"]], "phase": "finished", "result": result,
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    if record.get("path"):
        finished["after"] = file_state(Path(record["path"]))
        # A refused stale edit must not acknowledge the external change as read.
        if stale:
            finished["after"] = previous
        elif name == "read" and (not result.get("ok") or finished["after"] != record["before"]):
            finished["after"] = {"unknown": "读取失败或期间内容变化，需重新读取"}
    session.record_operation(finished)
    return result
