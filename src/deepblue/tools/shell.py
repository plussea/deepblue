from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import uuid

from .base import MAX_OUTPUT_BYTES, Tool, ToolContext

MAX_LOG_BYTES = 8 * 1024 * 1024


def stop_process(process: subprocess.Popen, job=None):
    if job is not None:
        job.close()
    elif os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def shell(context: ToolContext, args: dict) -> dict:
    command = args["command"]
    timeout = min(args.get("timeout", context.shell_timeout), context.shell_timeout)
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise ValueError("未找到 PowerShell，请安装或加入 PATH。")
        prefix = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding; "
        suffix = "\n$deepblueSucceeded = $?; if (-not $deepblueSucceeded) { if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1 }"
        argv = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + command + suffix]
        process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        argv = ["/bin/sh", "-c", command]
        process_options = {"start_new_session": True}
    context.artifacts.mkdir(parents=True, exist_ok=True)
    log_path = context.artifacts / ("shell-" + uuid.uuid4().hex + ".log")
    env = os.environ.copy()
    # The model's shell commands do not need the provider credential.
    env.pop("DEEPSEEK_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    reason = None
    with log_path.open("wb") as output:
        process = subprocess.Popen(argv, cwd=context.cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, **process_options)
        job = None
        try:
            if os.name == "nt":
                from .process import WindowsJob
                job = WindowsJob(process)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    reason = "命令超时，已终止进程树。"
                    stop_process(process, job)
                    break
                if log_path.stat().st_size > MAX_LOG_BYTES:
                    reason = "命令输出超过 8 MiB，已终止进程树。"
                    stop_process(process, job)
                    break
                time.sleep(0.05)
        except BaseException:
            stop_process(process, job)
            raise
        finally:
            if job is not None:
                job.close()
            elif os.name != "nt":
                # Background services are unsupported; dispose of descendants too.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    size = log_path.stat().st_size
    with log_path.open("rb") as output:
        output.seek(max(0, size - MAX_OUTPUT_BYTES))
        tail = output.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")
    result = {"ok": process.returncode == 0 and reason is None,
              "exit_code": process.returncode, "output": tail,
              "truncated": size > MAX_OUTPUT_BYTES, "log_path": str(log_path)}
    if reason:
        result["error"] = reason
    return result


TOOL = Tool("shell", "在项目目录执行命令（Windows: PowerShell；其他: /bin/sh）。无交互输入，不支持后台常驻服务。返回退出码和合并输出尾部，完整输出见 log_path。", {
    "command": {"type": "string", "minLength": 1},
    "timeout": {"type": "number", "minimum": 0.1},
}, ["command"], shell)
