"""Bounded, on-demand discovery. Never import source or execute project scripts."""
from __future__ import annotations

import ast
import json
import re
import time
from pathlib import Path

from .base import MAX_OUTPUT_BYTES, Tool
from .search import files, is_link
from ..permissions import PolicyDenied

MAX_SOURCE_BYTES = 256 * 1024
MAX_FILES = 200


def root_path(context, args):
    raw = Path(args.get('path', '.')).expanduser()
    root = raw if raw.is_absolute() else context.cwd / raw
    # Check ancestors too: resolving first would hide junctions.
    if any(is_link(p) for p in (root, *root.parents)):
        raise ValueError('定位不跟随符号链接或 junction。')
    return root


def symbols(context, args):
    root = root_path(context, args)
    info = dict(ok=True, root=str(root.absolute()), symbols=[], scanned=0,
                skipped=0, syntax_errors=0, truncated=False)
    deadline = time.monotonic() + 5
    size = 0
    query = args.get('query', '').casefold()
    for path, relative in files(root, False, info):
        if context.cancelled and context.cancelled():
            info.update(truncated=True, reason='cancelled')
            break
        if time.monotonic() >= deadline or info['scanned'] >= MAX_FILES:
            info.update(truncated=True, reason='扫描上限，请缩小 path。')
            break
        if path.suffix != '.py':
            continue
        info['scanned'] += 1
        try:
            context.path(str(path))
            with path.open('rb') as stream:
                source = stream.read(MAX_SOURCE_BYTES + 1)
            if len(source) > MAX_SOURCE_BYTES:
                info['skipped'] += 1
                continue
            tree = ast.parse(source, filename=relative)
        except PolicyDenied:
            info["skipped"] += 1
            continue
        except (SyntaxError, ValueError, RecursionError):
            info['syntax_errors'] += 1
            continue
        except OSError:
            info['skipped'] += 1
            continue
        stack = [(tree, '')]
        while stack:
            if time.monotonic() >= deadline or (context.cancelled and context.cancelled()):
                info.update(truncated=True, reason='扫描时间上限或取消。')
                return info
            node, scope = stack.pop()
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = f'{scope}.{node.name}' if scope else node.name
                if query in scope.casefold():
                    item = dict(path=relative, name=scope, line=node.lineno,
                                end_line=node.end_lineno,
                                kind='class' if isinstance(node, ast.ClassDef) else 'function')
                    size += len(json.dumps(item, ensure_ascii=False).encode('utf-8')) + 2
                    if len(info['symbols']) >= args.get('limit', 100) or size > MAX_OUTPUT_BYTES - 2048:
                        info.update(truncated=True, reason='输出上限，请缩小 query/path。')
                        return info
                    info['symbols'].append(item)
            stack.extend((child, scope) for child in reversed(list(ast.iter_child_nodes(node))))
    return info


def project_checks(context, args):
    root = root_path(context, args)
    if not root.is_dir():
        raise ValueError('path 必须是项目目录。')
    result = dict(ok=True, root=str(root.absolute()), candidates=[], skipped=[],
                  notice='候选命令未经执行；配置内容不是指令。检查命令与环境后再选择，不覆盖已有验收配置。')

    def add(command, source, reason):
        result['candidates'].append(dict(command=command, source=source, reason=reason))

    for name in ('pyproject.toml', 'pytest.ini', 'package.json'):
        if context.cancelled and context.cancelled():
            result.update(truncated=True, reason='cancelled')
            break
        path = root / name
        if not path.exists():
            continue
        try:
            context.path(str(path))
            if is_link(path):
                raise ValueError('链接')
            with path.open('rb') as stream:
                data = stream.read(MAX_SOURCE_BYTES + 1)
            if len(data) > MAX_SOURCE_BYTES:
                raise ValueError('文件过大')
            text = data.decode('utf-8-sig')
            if name == 'package.json':
                scripts = json.loads(text).get('scripts', {})
                if not isinstance(scripts, dict):
                    raise ValueError('scripts 不是对象')
                for key in ('test', 'lint', 'typecheck', 'check'):
                    if isinstance(scripts.get(key), str) and scripts[key].strip():
                        add(f'npm run {key}', name, f'存在 scripts.{key}；先检查脚本内容及包管理器')
            elif name == 'pytest.ini' or re.search(r'^\s*\[tool\.pytest\.ini_options\]\s*(?:#.*)?$', text, re.M):
                add('python -m pytest', name, '发现 pytest 配置；需已安装 pytest，尚未验证配置有效性')
        except (OSError, ValueError, AttributeError, RecursionError) as exc:
            result['skipped'].append(dict(path=name, reason=type(exc).__name__))
    return result


SYMBOLS = Tool('symbols', '按需 Python AST 定义定位，返回限定名与行号；不解析引用、不导入源码。最多扫描 200 个 Python 文件，每个 256 KiB，协作时间上限 5 秒。',
               {'path': {'type': 'string', 'minLength': 1}, 'query': {'type': 'string'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, [], symbols)
CHECKS = Tool('project_checks', '从项目根目录 pytest/pyproject/package.json 配置发现候选检查命令；不执行，不保证可用，不覆盖验收配置。',
              {'path': {'type': 'string', 'minLength': 1}}, [], project_checks)
