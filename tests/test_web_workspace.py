import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_web
from deepblue.session import Session
from deepblue.web import Workspace, storage_home
from deepblue.web_sessions import atomic_json
from deepblue.web_review import status, diff


class WorkspaceTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    shutdown = test_web.WebTests.shutdown
    request = test_web.WebTests.request
    wait_job = test_web.WebTests.wait_job

    def make_session(self, text='first'):
        session = Session.create(self.workspace.home, self.root, 'fake', 'system')
        session.add({'role': 'user', 'content': text})
        session.close()
        return session

    def test_web_default_storage_is_project_local_and_explicit_overrides(self):
        with patch.dict(os.environ, {'DEEPBLUE_HOME': ''}):
            self.assertEqual(storage_home(self.root), self.root / '.deepblue')
        with patch.dict(os.environ, {'DEEPBLUE_HOME': str(self.base / 'env-home')}):
            self.assertEqual(storage_home(self.root), self.base / 'env-home')
            self.assertEqual(storage_home(self.root, self.base / 'explicit'), self.base / 'explicit')

    def test_unwritable_storage_fails_before_worker_or_model_request(self):
        with patch('deepblue.web.tempfile.TemporaryFile', side_effect=PermissionError(5, 'Access denied')):
            with self.assertRaisesRegex(ValueError, '会话存储目录不可写.*--home'):
                Workspace(self.root, self.base / 'unwritable')
        blocked = self.base / 'file-not-directory'
        blocked.write_text('keep')
        with self.assertRaisesRegex(ValueError, '会话存储目录不可写'):
            Workspace(self.root, blocked)
        self.assertEqual(blocked.read_text(), 'keep')

    def test_idempotency_survives_completion_and_restart(self):
        data = {'prompt': 'one execution', 'request_id': 'a' * 32}
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}):
            first = self.request('/api/run', data)[1]
            self.assertEqual(self.request('/api/run', data)[1]['job_id'], first['job_id'])
            self.wait_job()
            self.assertEqual(self.request('/api/run', data)[1]['job_id'], first['job_id'])
        restarted = Workspace(self.root, self.workspace.home)
        self.assertEqual(restarted.start(data)['job_id'], first['job_id'])
        with self.assertRaises(ValueError):
            restarted.start({**data, 'prompt': 'different'})
        state = self.workspace.state(first['session_id'])
        self.assertEqual(sum(m['role'] == 'user' for m in state['messages']), 1)

    def test_restart_marks_unknown_without_replaying(self):
        session = self.make_session()
        job_id = 'b' * 32
        atomic_json(self.workspace.job_dir / (job_id + '.json'), dict(id=job_id, session_id=session.header['id'],
            request_id='c' * 32, request_hash='', finished=False, status='running', created=1))
        before = session.path.read_bytes()
        restored = Workspace(self.root, self.workspace.home)
        data = restored.events(0, job_id, True)
        self.assertEqual(data['status'], 'interrupted')
        self.assertEqual(data['outcome']['execution_status'], 'unknown')
        self.assertTrue((restored.job_dir / (job_id + '.cancel')).exists())
        self.assertEqual(session.path.read_bytes(), before)

    def test_snapshot_handles_event_overflow_and_scoped_cancel(self):
        with patch.object(self.workspace, 'run_worker'):
            first = self.workspace.start({'prompt': 'snapshot'})
        job = self.workspace.job
        for _ in range(2100):
            self.workspace.append_event(job, 'text_delta', {'text': 'x'})
        result = self.workspace.events(0, first['job_id'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['snapshot'][-1]['data']['text'], 'x' * 2100)
        self.assertEqual(self.request('/api/cancel', {})[0], 400)
        self.assertEqual(self.request('/api/cancel', {'job_id': 'f' * 32})[0], 400)
        self.assertFalse(job['cancel_path'].exists())
        self.request('/api/cancel', {'job_id': first['job_id']})
        self.assertTrue(job['cancel_path'].exists())
        job['finished'] = True

    def test_running_session_read_and_incremental_index(self):
        session = Session.create(self.workspace.home, self.root, 'fake', 'system')
        self.addCleanup(session.close)
        # A real writer owns the OS lock throughout the read.
        session.add({'role': 'user', 'content': 'old'})
        self.assertEqual(self.request('/api/session?id=' + session.header['id'])[0], 200)
        consumed = self.workspace.index.bytes_read
        self.workspace.state(session.header['id'])
        self.assertEqual(self.workspace.index.bytes_read, consumed)
        session.add({'role': 'assistant', 'content': 'new'})
        self.assertEqual(self.workspace.state(session.header['id'])['messages'][-1]['content'], 'new')
        self.assertLess(self.workspace.index.bytes_read - consumed, 200)

    def test_history_pagination_content_search_metadata_export(self):
        session = self.make_session('needle in content')
        with session.path.open('ab') as file:
            for i in range(1005):
                file.write((json.dumps({'type': 'message', 'message': {'role': 'assistant', 'content': f'line {i}'}}) + '\n').encode())
        sid = session.header['id']
        newest = self.request('/api/session?id=' + sid)[1]
        self.assertEqual(len(newest['messages']), 100)
        older = self.request(f"/api/session?id={sid}&before={newest['before']}")[1]
        self.assertEqual(older['messages'][-1]['index'] + 1, newest['messages'][0]['index'])
        self.request('/api/session/edit', {'id': sid, 'title': 'renamed'})
        self.assertEqual(self.request('/api/sessions?q=needle')[1]['items'][0]['title'], 'renamed')
        self.request('/api/session/edit', {'id': sid, 'archived': True})
        self.assertEqual(self.request('/api/sessions?q=needle')[1]['total'], 0)
        self.assertEqual(self.request('/api/sessions?archived=1')[1]['total'], 1)
        self.assertEqual(self.request('/api/run', {'session_id': sid, 'prompt': 'no'})[0], 400)
        self.request('/api/session/edit', {'id': sid, 'archived': False})
        exported = json.loads(self.request(f'/api/export?id={sid}&format=json')[1]['content'])
        self.assertEqual(len(exported['messages']), 1007)
        self.assertIn('line 1004', self.request(f'/api/export?id={sid}&format=md')[1]['content'])

    def test_more_than_100_sessions_accessible(self):
        for i in range(105):
            self.make_session(str(i))
        page = self.request('/api/sessions?cursor=0&limit=100')[1]
        self.assertEqual(page['total'], 105)
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(len(self.request('/api/sessions?cursor=100')[1]['items']), 5)

    def test_configuration_is_persisted_masked_and_environment_owned(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': '', 'LLM_API_KEY': '', 'DEEPSEEK_MODEL': '', 'LLM_MODEL': ''}):
            code, data = self.request('/api/config', {'api_key': 'not-a-real-key', 'model': 'fixture', 'timeout': 2})
            self.assertEqual(code, 200)
            self.assertTrue(data['configured'])
            self.assertNotIn('not-a-real-key', json.dumps(data))
            self.assertEqual(self.request('/api/config/test', {})[0], 200)
            self.assertEqual(self.request('/api/config', {'timeout': float('nan')})[0], 400)
            self.assertEqual(self.request('/api/config', {'base_url': 'https://user:pass@example.com'})[0], 400)
            with patch.object(self.workspace, 'run_worker') as worker:
                self.workspace.start({'prompt': 'frozen'})
                deadline = time.monotonic() + 2
                while not worker.called and time.monotonic() < deadline:
                    time.sleep(.01)
                self.workspace.configure({'api_key': 'replacement', 'model': 'new'})
                self.assertEqual(worker.call_args.args[1]['model'], 'fixture')
                self.assertEqual(worker.call_args.args[2], 'not-a-real-key')
                self.workspace.job['finished'] = True
                saved = (self.workspace.job_dir / (self.workspace.job['id'] + '.json')).read_text(encoding='utf-8')
                self.assertNotIn('not-a-real-key', saved)
                self.assertNotIn('replacement', saved)
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'env-secret'}):
                self.assertEqual(self.request('/api/config', {'api_key': 'other'})[0], 400)
            self.workspace.configure({'api_key': ''})
            self.assertFalse(self.workspace.configuration()['configured'])

    def test_profiles_restart_reveal_and_key_isolation(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'env-fixture'}):
            data=dict(profile_id='custom',name='Custom',provider='openai-compatible',
                      model='another-model',base_url='https://example.com/v1',api_key='saved-fixture')
            code, public=self.request('/api/config',data)
            self.assertEqual(code,200)
            self.assertNotIn('saved-fixture',json.dumps(public))
            self.assertEqual(self.request('/api/config/key',{})[1]['api_key'],'saved-fixture')
            restarted=Workspace(self.root,self.workspace.home)
            self.assertEqual(restarted.api_key(),'saved-fixture')
            self.assertEqual(restarted.settings['provider'],'openai-compatible')
            self.assertNotIn('saved-fixture',restarted.profile_store.path.read_text(encoding='utf-8') if os.name=='nt' else '')
            snapshot=dict(restarted.settings)
            restarted.configure({'profile_id':'default'})
            self.assertEqual(restarted.api_key(),'env-fixture')
            self.assertEqual(restarted.key_for_settings(snapshot),'saved-fixture')
            restarted.configure({'profile_id':'custom'})
            restarted.configure({'base_url':'https://another.example/v1'})
            self.assertEqual(restarted.api_key(),'')
            self.assertEqual(restarted.key_for_settings(snapshot),'')
            with self.assertRaises(ValueError): restarted.safe_path(str(restarted.profile_store.path))

    def test_evidence_log_registered_and_stale(self):
        from test_tools import python_command
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}):
            result = self.request('/api/run', {'prompt': 'verify', 'verify': python_command("print('evidence output')")})[1]
            self.wait_job()
        sid = result['session_id']
        state = self.workspace.state(sid)
        self.assertEqual(state['verification_status'], 'passed')
        evidence_id = state['evidence'][0]['id']
        log = self.request(f'/api/evidence?id={sid}&evidence_id={evidence_id}')[1]
        self.assertIn('evidence output', log['content'])
        self.assertEqual(self.request(f'/api/evidence?id={sid}&evidence_id=../../outside')[0], 400)
        (self.root / 'change.py').write_text('changed')
        self.assertEqual(self.workspace.state(sid)['verification_status'], 'stale')

    def test_git_staged_unstaged_untracked_and_preexisting_baseline(self):
        def run(*args):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        run('init', '-q')
        path = self.root / 'code.py'
        path.write_text('original\n')
        run('add', 'code.py')
        run('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
        path.write_text('staged\n')
        run('add', 'code.py')
        path.write_text('unstaged\n')
        (self.root / 'new.txt').write_text('new text')
        info = status(self.workspace)
        self.assertEqual(next(x for x in info['files'] if x['path'] == 'code.py')['index'], 'M')
        self.assertIn('+staged', diff(self.workspace, 'code.py', True)['content'])
        self.assertIn('+unstaged', diff(self.workspace, 'code.py')['content'])
        self.assertIn('new text', diff(self.workspace, 'new.txt')['content'])
        with patch.object(self.workspace, 'run_worker'):
            result = self.workspace.start({'prompt': 'baseline'})
        self.workspace.job['finished'] = True
        review = self.workspace.review(result['session_id'])
        self.assertEqual(len(review['baseline']['files']), 2)
        with self.assertRaises(ValueError):
            diff(self.workspace, '../outside')

    def test_non_git_review_and_file_reference_boundary(self):
        self.assertFalse(self.workspace.review()['available'])
        (self.root / '中文 文件.py').write_text('hello', encoding='utf-8')
        self.assertEqual(self.workspace.search_files('中文')['items'][0]['path'], '中文 文件.py')
        self.assertEqual(self.request('/api/run', {'prompt': 'read @"../outside"'})[0], 400)
        with patch.object(self.workspace, 'run_worker') as worker:
            self.workspace.start({'prompt': 'read @"中文 文件.py"'})
            deadline = time.monotonic() + 2
            while not worker.called and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIn('引用的项目文件', worker.call_args.args[1]['prompt'])
            self.workspace.job['finished'] = True

    def test_live_tool_log_paging_and_path_confinement(self):
        session = self.make_session()
        sid = session.header['id']
        log = session.artifacts / 'output.log'
        log.write_text('a' * 40000, encoding='utf-8')
        session = Session.load(session.path, self.root)
        session.record_operation({'operation_id': 'operation', 'name': 'shell', 'phase': 'started', 'log_path': str(log)})
        session.close()
        with patch.object(self.workspace, 'run_worker'):
            self.workspace.start({'prompt': 'live', 'session_id': sid})
        first = self.workspace.tool_log(sid, 'operation')
        self.assertEqual(len(first['content']), 32000)
        self.assertTrue(first['live'])
        second = self.workspace.tool_log(sid, 'operation', first['next'])
        self.assertEqual(len(second['content']), 8000)
        self.assertEqual(second['next'], 40000)
        self.workspace.job['finished'] = True
        self.assertIsNone(self.workspace.tool_log(sid, 'operation', 40000)['next'])
        session = Session.load(session.path, self.root)
        session.record_operation({'operation_id': 'outside', 'name': 'shell', 'phase': 'finished', 'log_path': str(self.base / 'outside')})
        session.close()
        self.assertEqual(self.request(f'/api/tool-log?id={sid}&operation_id=outside')[0], 400)

    def test_authenticated_sse_snapshot(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}):
            result = self.request('/api/run', {'prompt': 'sse'})[1]
            self.wait_job()
        code, raw = self.request('/api/stream?job_id=' + result['job_id'] + '&snapshot=1')
        self.assertEqual(code, 200)
        packet = json.loads(raw.decode().split('data: ', 1)[1])
        self.assertTrue(packet['finished'])
        self.assertTrue(packet['snapshot'])
        self.assertEqual(self.request('/api/stream?job_id=' + result['job_id'], **{'X-Deepblue-Token': 'bad'})[0], 403)

    def test_server_process_restart_cancels_orphan_without_replay(self):
        import sys
        from urllib.request import Request
        from test_agent import tool_call
        from test_tools import python_command
        marker = self.root / 'effect.txt'
        command = python_command("from pathlib import Path; import time; p=Path('effect.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(15)")
        self.payload = {'role': 'assistant', 'content': None, 'tool_calls': [tool_call('shell', {'command': command})]}
        process = subprocess.Popen([sys.executable, '-m', 'deepblue.web', '--cwd', str(self.root), '--home', str(self.workspace.home),
            '--port', '0', '--base-url', self.workspace.settings['base_url']], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env={**os.environ, 'DEEPSEEK_API_KEY': 'fixture'},
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        url = process.stdout.readline().strip().split(' — ')[-1]
        with self.opener.open(url + '/api/bootstrap', timeout=5) as response:
            token = json.load(response)['token']
        request = Request(url + '/api/run', data=json.dumps({'prompt': 'restart fixture'}).encode(),
                          headers={'Content-Type': 'application/json', 'X-Deepblue-Token': token})
        with self.opener.open(request, timeout=5) as response:
            job = json.load(response)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(marker.exists())
        process.kill()
        process.communicate(timeout=10)
        restored = Workspace(self.root, self.workspace.home)
        self.assertEqual(restored.events(0, job['job_id'])['status'], 'interrupted')
        # The orphan notices the cancellation marker; new jobs cannot bypass its OS lock.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                session = Session(restored.session_path(job['session_id']))
                session.close()
                break
            except ValueError:
                time.sleep(.05)
        else:
            self.fail('orphan did not release session lock')
        self.assertEqual(marker.read_text(), 'x')
        self.assertEqual(restored.events(0, job['job_id'])['status'], 'interrupted')

    def test_redaction_does_not_corrupt_json_and_git_subdirectory(self):
        self.workspace.memory_key = 'a"key'
        self.assertEqual(self.workspace.redact({'status': 'a"key', 'flag': False}), {'status': '[REDACTED]', 'flag': False})
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True)
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'new.txt').write_text('data')
        workspace = Workspace(nested, self.workspace.home)
        self.assertEqual(status(workspace)['files'][0]['path'], 'new.txt')
        self.assertIn('data', diff(workspace, 'new.txt')['content'])


if __name__ == '__main__':
    unittest.main()
