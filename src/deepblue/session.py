from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Message


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_sessions(home: Path, cwd: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(cwd.resolve())).encode()).hexdigest()[:20]
    return home / "sessions" / key


class Session:
    """Append-only transcript, locked by the OS for the lifetime of its owner."""

    def __init__(self, path: Path):
        self.path = path
        self.messages: list[Message] = []
        self.header: dict = {}
        self._lock = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire()

    def _acquire(self):
        lock = self.path.with_suffix(".lock").open("a+b")
        try:
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise ValueError("该会话正在被另一个深蓝进程使用。") from None
        self._lock = lock

    @classmethod
    def create(cls, home: Path, cwd: Path, model: str, system_prompt: str) -> Session:
        session_id = uuid.uuid4().hex
        session = cls(project_sessions(home, cwd) / (session_id + ".jsonl"))
        try:
            session.header = {"type": "session", "version": 1, "id": session_id,
                              "cwd": str(cwd.resolve()), "model": model, "created_at": now()}
            session._append(session.header)
            session.add({"role": "system", "content": system_prompt})
            return session
        except BaseException:
            session.close()
            raise

    @classmethod
    def latest(cls, home: Path, cwd: Path) -> Session:
        paths = list(project_sessions(home, cwd).glob("*.jsonl"))
        if not paths:
            raise ValueError("当前目录没有可恢复的会话。先运行 deepblue 创建会话。")
        return cls.load(max(paths, key=lambda path: path.stat().st_mtime_ns), cwd)

    @classmethod
    def load(cls, path: Path, cwd: Path) -> Session:
        session = cls(path)
        try:
            # Only a non-newline-terminated final record may be repaired. Corruption
            # in a committed record fails explicitly instead of hiding data loss.
            with path.open("r+b") as file:
                while True:
                    start = file.tell()
                    raw = file.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        file.truncate(start)
                        break
                    record = json.loads(raw)
                    if not session.header:
                        if record.get("type") != "session" or record.get("version") != 1:
                            raise ValueError("不支持的会话格式。")
                        session.header = record
                    elif record.get("type") == "message":
                        session.messages.append(record["message"])
            if not session.header or not session.messages:
                raise ValueError("会话文件不完整。")
            if Path(session.header["cwd"]).resolve() != cwd.resolve():
                raise ValueError("会话工作目录不匹配。")
            session.recover_pending()
            return session
        except BaseException:
            session.close()
            raise

    @property
    def artifacts(self) -> Path:
        path = self.path.with_suffix("")
        path.mkdir(exist_ok=True)
        return path

    def _append(self, record: dict):
        with self.path.open("ab") as file:
            file.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())

    def add(self, message: Message):
        self._append({"type": "message", "at": now(), "message": message})
        self.messages.append(message)

    def record_usage(self, usage: dict):
        if usage:
            self._append({"type": "usage", "at": now(), "usage": usage})

    def recover_pending(self):
        pending = {}
        for message in self.messages:
            if message["role"] == "assistant":
                for call in message.get("tool_calls", []):
                    pending[call["id"]] = call
            elif message["role"] == "tool":
                pending.pop(message["tool_call_id"], None)
        for call_id in pending:
            self.add({"role": "tool", "tool_call_id": call_id, "content": json.dumps({
                "ok": False, "error": "执行中断，结果未知。该调用不会自动重放；继续前先检查文件或命令副作用。"
            }, ensure_ascii=False)})
        return len(pending)

    def close(self):
        if self._lock is not None:
            self._lock.close()
            self._lock = None
