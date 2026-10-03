"""Application-level tool policy, not a concurrent-adversary OS sandbox."""
import os
from pathlib import Path

MODES = {'trusted', 'workspace', 'read-only'}
FILES = {'read', 'write', 'edit', 'find', 'grep', 'symbols', 'project_checks'}


class PolicyDenied(ValueError):
    pass


def check_path(context, value):
    raw = Path(value).expanduser()
    raw = raw if raw.is_absolute() else context.cwd / raw
    if context.permission_mode == 'trusted':
        return raw.resolve()
    root = context.cwd.resolve()
    candidate = Path(os.path.abspath(raw))
    if not candidate.is_relative_to(root):
        raise PolicyDenied('权限拒绝：路径超出工作区。')
    relative = candidate.relative_to(root)
    if any(p.casefold() in {'.git', '.hg', '.svn', '.deepblue'} or ':' in p for p in relative.parts):
        raise PolicyDenied('权限拒绝：仓库元数据、运行数据或特殊路径。')
    for protected in context.protected_paths:
        if candidate.is_relative_to(Path(protected).resolve()):
            raise PolicyDenied('权限拒绝：运行存储目录。')
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            stat = part.lstat()
            if part.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                raise PolicyDenied('权限拒绝：符号链接或 junction。')
        if part == root:
            break
    if candidate.is_file() and candidate.stat().st_nlink > 1:
        raise PolicyDenied('权限拒绝：多重硬链接文件。')
    return candidate


def authorize(context, name, args):
    if context.permission_mode not in MODES:
        raise PolicyDenied('无效权限模式。')
    if context.permission_mode == 'trusted':
        return
    if name not in FILES | {'task_update', 'load_skill'}:
        raise PolicyDenied('权限拒绝：受限模式不运行 Shell 或未知扩展工具。')
    if context.permission_mode == 'read-only' and name in {'write', 'edit'}:
        raise PolicyDenied('权限拒绝：只读模式不修改项目文件。')
    if name in FILES:
        check_path(context, args.get('path', '.'))
