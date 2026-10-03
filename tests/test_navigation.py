import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.tools import ToolContext, create_tools
from deepblue.tools.navigation import MAX_SOURCE_BYTES
from deepblue.recovery import execute_recorded
from deepblue.session import Session
from deepblue.tool_metrics import operation_metrics


class NavigationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.context = ToolContext(self.root, self.root / 'logs')
        self.tools = create_tools(self.context)

    def call(self, name, **args):
        return self.tools.execute(name, json.dumps(args))

    def test_qualified_definitions_without_execution(self):
        (self.root / 'example.py').write_text("raise RuntimeError('never execute')\nclass A:\n    async def work(self):\n        def inner(): pass\n", encoding='utf-8')
        result = self.call('symbols', query='A.work')
        self.assertEqual([s['name'] for s in result['symbols']], ['A.work', 'A.work.inner'])
        self.assertEqual(result['symbols'][0]['line'], 3)

    def test_skips_dependencies_invalid_and_large_files(self):
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules/ignored.py').write_text('def nope(): pass')
        (self.root / 'bad.py').write_text('def :')
        (self.root / 'big.py').write_bytes(b' ' * (MAX_SOURCE_BYTES + 1))
        result = self.call('symbols')
        self.assertEqual(result['symbols'], [])
        self.assertEqual(result['syntax_errors'], 1)
        self.assertEqual(result['skipped'], 1)

    def test_output_scan_limits_and_cancel(self):
        (self.root / 'a.py').write_text('def a(): pass\ndef b(): pass')
        self.assertTrue(self.call('symbols', limit=1)['truncated'])
        with patch('deepblue.tools.navigation.MAX_FILES', 0):
            self.assertEqual(self.call('symbols')['scanned'], 0)
        self.context.cancelled = lambda: True
        self.assertTrue(self.call('symbols')['truncated'])

    def test_links_rejected_before_resolution(self):
        with patch('deepblue.tools.navigation.is_link', return_value=True):
            self.assertFalse(self.call('symbols')['ok'])
            self.assertFalse(self.call('project_checks')['ok'])

    def test_check_candidates_are_not_executed(self):
        (self.root / 'package.json').write_text(json.dumps({'scripts': {'test': 'never-run-me', 'lint': 'x', 'start': 'x'}}))
        (self.root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n')
        result = self.call('project_checks')
        self.assertEqual([c['command'] for c in result['candidates']], ['python -m pytest', 'npm run test', 'npm run lint'])
        self.assertEqual(self.call('project_checks', path='package.json')['ok'], False)

    def test_malformed_configs_reported(self):
        for text in ('{', '[]', '{"scripts":[]}'):
            (self.root / 'package.json').write_text(text)
            result = self.call('project_checks')
            self.assertEqual(len(result['skipped']), 1)
            self.assertEqual(result['candidates'], [])

    def test_repeat_metrics_pages_changes_failure_and_reload(self):
        session = Session.create(self.root / 'home', self.root, 'fake', 'system')
        self.addCleanup(session.close)
        session.record_run({'run_id': 'one'})
        def call(name, **args):
            return execute_recorded(session, self.tools, {'id': 'call', 'function': {'name': name, 'arguments': json.dumps(args)}})
        (self.root / 'a.txt').write_text('first\nsecond\n')
        call('read', path='a.txt', limit=1)
        call('read', path='a.txt', offset=1, limit=1)
        call('read', path='a.txt', offset=2, limit=1)
        (self.root / 'a.txt').write_text('changed\nsecond\n')
        call('read', path='a.txt', limit=1)
        call('read', path='missing')
        call('read', path='missing')
        call('write', path='a.txt', content='fixed')
        metrics = operation_metrics(session, 'one')
        self.assertEqual(metrics['tool_calls'], 7)
        self.assertEqual(metrics['repeated_reads'], 1)
        self.assertEqual(metrics['failed_calls'], 2)
        self.assertEqual(metrics['repeated_failures'], 1)
        self.assertEqual(metrics['calls_before_first_file_change'], 6)
        session.close()
        loaded = Session.load(session.path, self.root)
        self.addCleanup(loaded.close)
        self.assertEqual(operation_metrics(loaded, 'one'), metrics)
        self.assertEqual(operation_metrics(loaded, 'two')['tool_calls'], 0)

    def test_shell_invalidates_repeat_read_history(self):
        class SessionStub:
            operations = {
                'a': dict(run_id='r', phase='finished', name='read', result={'ok': True}, read_signature='x'),
                'b': dict(run_id='r', phase='finished', name='shell', result={'ok': False}),
                'c': dict(run_id='r', phase='finished', name='read', result={'ok': True}, read_signature='x'),
                'd': dict(run_id='r', phase='started', name='read'),
            }
        self.assertEqual(operation_metrics(SessionStub(), 'r')['repeated_reads'], 0)
