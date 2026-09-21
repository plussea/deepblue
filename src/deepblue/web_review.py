"""Read-only Git review. argv only; hooks/external diff/textconv are never invoked."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .recovery import file_state


def git(cwd, args, limit=1048576):
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat'}
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(['git', '--no-pager', '--literal-pathspecs', '-c', 'core.quotePath=false', *args],
                                    cwd=cwd, env=env, stdout=output, stderr=errors, timeout=10, **flags)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Git 不可用或读取超时。') from None
        output.seek(0)
        raw = output.read(limit + 1)
        return result.returncode, raw[:limit], len(raw) > limit


def status(workspace):
    code, root_bytes, _ = git(workspace.cwd, ['rev-parse', '--show-toplevel'])
    if code:
        return {'available': False, 'files': [], 'reason': '当前目录不是 Git 工作区。'}
    repository = Path(root_bytes.decode('utf-8').strip()).resolve()
    prefix = workspace.cwd.relative_to(repository).as_posix()
    prefix = '' if prefix == '.' else prefix + '/'
    code, raw, truncated = git(workspace.cwd, ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.'])
    if code or truncated:
        raise ValueError('无法读取完整 Git 状态（超过 1 MiB 时拒绝部分结果）。')
    parts, files = raw.decode('utf-8', errors='replace').split('\0'), []
    i = 0
    while i < len(parts) and parts[i]:
        item = parts[i]
        code, path = item[:2], item[3:]
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        record = {'path': path, 'index': code[0], 'worktree': code[1], 'untracked': code == '??'}
        if 'R' in code or 'C' in code:
            i += 1
            original = parts[i]
            if not prefix or original.startswith(prefix):
                record['original_path'] = original[len(prefix):]
        try:
            record['observed'] = file_state(workspace.safe_path(path))
        except ValueError:
            record['observed'] = {'unknown': '文件超出当前目录'}
        files.append(record)
        i += 1
    return {'available': True, 'files': files}


def diff(workspace, path, staged=False):
    resolved = workspace.safe_path(path)
    info = status(workspace)
    item = next((x for x in info['files'] if x['path'] == path), None)
    if not item:
        raise ValueError('文件不在当前修改列表。')
    if item['untracked']:
        if staged:
            return {'content': '', 'truncated': False}
        with resolved.open('rb') as file:
            raw = file.read(256001)
        if b'\0' in raw:
            return {'content': '[未跟踪二进制文件]', 'truncated': False}
        return {'content': '未跟踪文件（当前内容）\n' + raw[:256000].decode('utf-8', errors='replace'), 'truncated': len(raw) > 256000}
    args = ['diff', '--no-ext-diff', '--no-textconv', '--no-color']
    if staged:
        args.append('--cached')
    args.extend(['--', path])
    if item.get('original_path'):
        workspace.safe_path(item['original_path'])
        args.append(item['original_path'])
    code, raw, truncated = git(workspace.cwd, args, 256000)
    if code:
        raise ValueError('Git diff 读取失败。')
    return {'content': raw.decode('utf-8', errors='replace'), 'truncated': truncated}
