"""Rebuildable, incremental read-only JSONL views. Never repairs a writer's tail."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .session import Session


def atomic_json(path, data):
    import os
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class SessionIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries = {}
        self.bytes_read = 0

    def read(self, path, cwd):
        # Callers hold lock while using the returned view.
        stat = path.stat()
        key = str(path)
        entry = self.entries.get(key)
        identity = (stat.st_dev, stat.st_ino)
        if (entry is None or entry['identity'] != identity or stat.st_size < entry['size']
                or (stat.st_size == entry['size'] and stat.st_mtime_ns != entry['mtime'])):
            entry = dict(session=Session(path, read_only=True), offset=0, identity=identity, size=0, mtime=0)
            self.entries[key] = entry
        session = entry['session']
        try:
            with path.open('rb') as file:
                file.seek(entry['offset'])
                while file.tell() < stat.st_size:
                    raw = file.readline()
                    self.bytes_read += len(raw)
                    if not raw.endswith(b'\n'):
                        break
                    record = json.loads(raw)
                    kind = record.get('type')
                    if not session.header:
                        if kind != 'session' or record.get('version') != 1 or Path(record['cwd']).resolve() != cwd:
                            raise ValueError('会话格式或工作目录不匹配。')
                        session.header = record
                    elif kind == 'message':
                        session.messages.append(record['message'])
                    elif kind == 'usage':
                        session._count_usage(record['usage'])
                    elif kind == 'compaction':
                        session._validate_compaction(record)
                        session.compaction = record
                        session.compaction_count += 1
                    elif kind == 'run':
                        session.last_run = record
                        session.runs[record['run_id']] = record
                    elif kind == 'task_state':
                        session.apply_task_state(record['state'])
                    elif kind == 'operation':
                        session.operations[record['operation_id']] = record
                    elif kind == 'tool_output':
                        session.tool_outputs[record['message_index']] = record['path']
                    entry['offset'] = file.tell()
            entry.update(size=stat.st_size, mtime=stat.st_mtime_ns)
            if not session.header:
                raise ValueError('会话尚未写入完整头部。')
            # Bound number of cached sessions; each can be rebuilt from its JSONL.
            if len(self.entries) > 128:
                oldest = next(iter(self.entries))
                if oldest != key:
                    del self.entries[oldest]
            return session
        except (ValueError, KeyError):
            self.entries.pop(key, None)
            raise


def message_page(session, before=None, limit=100):
    stop = len(session.messages) if before is None else min(max(0, int(before)), len(session.messages))
    start = max(0, stop - min(200, max(1, int(limit))))
    names = {c['id']: c['function']['name'] for m in session.messages for c in m.get('tool_calls', [])}
    messages = []
    operations = {op.get('message_index'): op for op in session.operations.values()}
    for index in range(start, stop):
        msg = dict(session.messages[index], index=index)
        if msg.get('role') == 'tool':
            msg['tool_name'] = names.get(msg.get('tool_call_id'), '工具')
            msg['elapsed_seconds'] = operations.get(index, {}).get('elapsed_seconds')
        # Large outputs are available separately by message index.
        if len(msg.get('content') or '') > 16000:
            msg['content'] = msg['content'][:16000]
            msg['content_truncated'] = True
        messages.append(msg)
    return dict(messages=messages, before=start if start else None, total=len(session.messages), history_truncated=start > 0)
