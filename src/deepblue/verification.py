"""Explicit, bounded checks. Evidence describes configured checks, not correctness."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .session import now
from .tools.base import ToolContext
from .tools.shell import shell


IGNORED = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def fingerprint(root: Path, excluded: tuple[Path, ...] = ()) -> dict:
    """Hash regular files, including untracked files; refuse ambiguous snapshots."""
    root = root.resolve()
    excluded = tuple(p.resolve() for p in excluded)
    if any(root.is_relative_to(p) for p in excluded):
        raise ValueError("验收工作区不能位于会话存储目录内。")
    files = {}
    total = 0
    entries = 0
    deadline = time.monotonic() + 10
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=lambda exc: (_ for _ in ()).throw(exc)):
        base = Path(directory)
        entries += len(dirs) + len(names)
        if entries > 20000 or time.monotonic() > deadline:
            raise ValueError("工作区指纹超过 20000 目录项 / 10 秒限制。")
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and not any(
            (base / d).resolve().is_relative_to(p) for p in excluded))
        for name in dirs + sorted(names):
            path = base / name
            if any(path.resolve().is_relative_to(p) for p in excluded):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("工作区含链接或联接点，无法确认内容指纹：" + str(path))
            if path.is_dir():
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("工作区含非常规文件：" + str(path))
            total += info.st_size
            if total > 128 * 1024 * 1024 or len(files) >= 20000 or time.monotonic() > deadline:
                raise ValueError("工作区指纹超过 128 MiB / 20000 文件 / 10 秒限制。")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    if time.monotonic() > deadline or source.tell() > info.st_size:
                        raise ValueError("文件读取超时或生成指纹时文件增长：" + str(path))
                    digest.update(chunk)
            after = path.stat()
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("生成指纹时文件发生变化：" + str(path))
            files[path.relative_to(root).as_posix()] = digest.hexdigest()
    encoded = json.dumps(files, sort_keys=True).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": files,
            "root": str(root), "excluded_names": sorted(IGNORED),
            "excluded_paths": [str(p) for p in excluded]}


@dataclass
class VerificationConfig:
    command: str
    cwd: Path
    timeout: float = 120
    max_repairs: int = 1

    def __post_init__(self):
        self.cwd = self.cwd.resolve()
        if not self.command.strip():
            raise ValueError("验收命令不能为空。")
        if not math.isfinite(self.timeout) or self.timeout < 0.1:
            raise ValueError("验收超时必须是至少 0.1 秒的有限数值。")
        if not 0 <= self.max_repairs <= 10:
            raise ValueError("验收修复次数必须在 0–10 之间。")
        if not self.cwd.is_dir():
            raise ValueError("验收目录不存在。")


def verify(config: VerificationConfig, root: Path, artifacts: Path,
           excluded: tuple[Path, ...], task_id: str, run_id: str, cancelled=None) -> dict:
    evidence = {"verification_id": uuid.uuid4().hex, "task_id": task_id,
                "run_id": run_id, "at": now(), "command": config.command,
                "cwd": str(config.cwd), "timeout": config.timeout,
                "verification_scope": "configured_checks", "status": "running"}
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / ("verification-" + evidence["verification_id"] + ".json")
    evidence["evidence_path"] = str(path)

    def save():
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(evidence, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)

    save()  # An interrupted process leaves explicitly unfinished evidence.
    started = time.monotonic()
    try:
        if not config.cwd.is_relative_to(root.resolve()):
            raise ValueError("验收目录必须位于任务工作区内，才能关联工作区指纹。")
        evidence["workspace_before"] = fingerprint(root, excluded)
        result = shell(ToolContext(config.cwd, artifacts, config.timeout, cancelled=cancelled), {"command": config.command})
        evidence.update(result)
        evidence["workspace_after"] = fingerprint(root, excluded)
        if result.get("error"):
            evidence["status"] = "error"
        elif not result["ok"]:
            # Arbitrary shell exit codes cannot reliably distinguish missing dependencies
            # from assertion failures. Preserve the output for classification by callers.
            evidence["status"] = "failed"
        elif evidence["workspace_before"]["sha256"] != evidence["workspace_after"]["sha256"]:
            evidence["status"] = "stale"
            evidence["error"] = "验证期间工作区内容改变，不能确认同一版本通过。"
        else:
            evidence["status"] = "passed"
    except KeyboardInterrupt:
        evidence["status"] = "cancelled"
    except (OSError, ValueError) as exc:
        evidence.update(status="error", error=str(exc))
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    save()
    return evidence


def current_status(record: dict | None, root: Path, excluded: tuple[Path, ...]) -> str:
    if not record:
        return "unverified"
    status = record["verification_status"]
    if status != "passed":
        return status
    try:
        previous = record["evidence"][-1]["workspace_after"]["sha256"]
        return "passed" if fingerprint(root, excluded)["sha256"] == previous else "stale"
    except (OSError, ValueError, KeyError, IndexError):
        return "stale"
