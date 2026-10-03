"""Cross-process transactional inbox. Claims are never replayed automatically."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class MessageQueue:
    def __init__(self, path):
        self.path = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, kind TEXT, job_id TEXT, session_id TEXT, prompt TEXT, status TEXT, settings TEXT, created REAL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE messages SET status='held' WHERE status='pending'")
            db.execute("UPDATE messages SET status='unknown' WHERE status='claimed'")

    def list(self):
        with self.db() as db:
            return [{k: row[k] for k in ('id', 'kind', 'job_id', 'session_id', 'prompt', 'status', 'created')}
                    for row in db.execute('SELECT * FROM messages ORDER BY created DESC LIMIT 100')]

    def add(self, kind, job_id, session_id, prompt, settings, identity=None):
        if not isinstance(kind, str) or kind not in {'steering', 'follow-up'} or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 64000:
            raise ValueError('队列类型无效或消息为空/过长。')
        identity = identity or uuid.uuid4().hex
        import re
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
            raise ValueError('无效队列请求 ID。')
        with self.db() as db:
            old = db.execute('SELECT * FROM messages WHERE id=?', (identity,)).fetchone()
            if old:
                if any(old[k] != v for k, v in [('kind', kind), ('job_id', job_id), ('session_id', session_id), ('prompt', prompt)]):
                    raise ValueError('队列 ID 已用于不同内容。')
                return identity
            if db.execute("SELECT count(*) FROM messages WHERE status IN ('pending','held','claimed')").fetchone()[0] >= 32:
                raise ValueError('队列上限为 32 条。')
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)',
                       (identity, kind, job_id, session_id, prompt, 'pending', json.dumps(settings), time.time()))
        return identity

    def edit(self, identity, prompt=None, cancel=False):
        if not isinstance(identity, str):
            raise ValueError("无效消息 ID。")
        if prompt is not None and (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 64000):
            raise ValueError('消息不能为空或超过 64000 字符。')
        with self.db() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (identity,)).fetchone()
            if not row or row['status'] not in {'pending', 'held'}:
                raise ValueError('消息已被领取或结束，不能修改。')
            db.execute('UPDATE messages SET prompt=?, status=? WHERE id=?',
                       (prompt if prompt is not None else row['prompt'], 'cancelled' if cancel else row['status'], identity))

    def claim(self, kind, job_id=None, identity=None):
        with self.db() as db:
            if identity:
                row = db.execute("SELECT * FROM messages WHERE id=? AND status IN ('pending','held') AND kind='follow-up'", (identity,)).fetchone()
            else:
                row = db.execute("SELECT * FROM messages WHERE kind=? AND job_id=? AND status='pending' ORDER BY created LIMIT 1", (kind, job_id)).fetchone()
            if not row:
                return None
            db.execute("UPDATE messages SET status='claimed' WHERE id=?", (row['id'],))
            return {**dict(row), 'settings': json.loads(row['settings'])}

    def finish(self, identity, status):
        if status not in {'applied', 'dispatched', 'unknown'}:
            raise ValueError('无效队列结束状态。')
        with self.db() as db:
            db.execute("UPDATE messages SET status=? WHERE id=? AND status='claimed'", (status, identity))

    def release_job(self, job_id, next_job=None):
        with self.db() as db:
            db.execute("UPDATE messages SET status='held' WHERE job_id=? AND kind='steering' AND status='pending'", (job_id,))
            if next_job:
                db.execute("UPDATE messages SET job_id=? WHERE job_id=? AND kind='follow-up' AND status='pending'", (next_job, job_id))
            else:
                db.execute("UPDATE messages SET status='held' WHERE job_id=? AND status='pending'", (job_id,))
