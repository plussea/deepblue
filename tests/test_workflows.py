import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.hooks import Hooks, HookError
from deepblue.message_queue import MessageQueue
from deepblue.forking import fork_session
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.tools.base import Tool
from deepblue.web import Workspace
from deepblue.web_projects import Projects, project_id
from deepblue.agent import Agent
from deepblue.config import Config
from test_agent import FakeClient, response, tool_call


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.home = self.base / 'home'

    def session(self):
        session = Session.create(self.home, self.root, 'fake', 'system')
        self.addCleanup(session.close)
        return session

    def test_hook_versions_copies_order_and_errors(self):
        hooks = Hooks()
        events = []
        hooks.on('tool.before', lambda e: e['data'].update(changed=True))
        hooks.on('tool.before', lambda e: events.append(e))
        hooks.on('tool.after', lambda e: events.append(e))
        self.assertEqual(hooks.call('tool', {'name': 'x'}, lambda: 5), 5)
        self.assertNotIn('changed', events[0]['data'])
        self.assertEqual(events[1]['data']['result'], 5)
        with self.assertRaises(ValueError):
            hooks.on('tool.before', lambda e: None, version=2)
        hooks.on('tool.before', lambda e: (_ for _ in ()).throw(RuntimeError('private')))
        with self.assertRaisesRegex(HookError, 'tool.before') as error:
            hooks.call('tool', {}, lambda: self.fail('must not execute'))
        self.assertNotIn('private', str(error.exception))

    def test_extension_registration_cannot_bypass_permissions(self):
        registry = create_tools(ToolContext(self.root, self.home, permission_mode='read-only'))
        calls = []
        registry.register(Tool('extension', '', {}, [], lambda c, a: calls.append(True)))
        self.assertFalse(registry.execute('extension', '{}')['ok'])
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            registry.register(registry.tools['read'])

    def test_run_and_steering_boundary_preserve_tool_pairs(self):
        session = self.session()
        registry = create_tools(ToolContext(self.root, session.artifacts))
        client = FakeClient([response(tool_call('write', {'path': 'a', 'content': 'ok'})), response(reason='stop')])
        calls = []
        registry.hooks.on('run.before', lambda e: calls.append('before'))
        registry.hooks.on('run.after', lambda e: calls.append('after'))
        count = [0]
        def incoming():
            count[0] += 1
            if count[0] == 2:
                self.assertEqual(session.messages[-1]['role'], 'tool')
                return [{'id': 'q', 'prompt': 'new direction'}]
            return []
        agent = Agent(Config(self.root, 'fake', home=self.home), client, registry, session, steering=incoming)
        agent.run('original')
        self.assertEqual(calls, ['before', 'after'])
        self.assertTrue(any(m.get('content') == 'new direction' for m in client.requests[-1]))
        self.assertEqual(session.task_state['goal'], 'original')

    def test_queue_edit_claim_race_and_restart(self):
        queue = MessageQueue(self.home / 'queue.db')
        identity = queue.add('steering', 'job', 'session', 'first', {})
        queue.edit(identity, 'edited')
        claimed = []
        def claim():
            claimed.append(queue.claim('steering', 'job'))
        threads = [threading.Thread(target=claim) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(item is not None for item in claimed), 1)
        self.assertEqual(next(item for item in claimed if item)['prompt'], 'edited')
        with self.assertRaises(ValueError): queue.edit(identity, 'too late')
        waiting = queue.add('follow-up', 'job', 'session', 'later', {})
        queue.recover()
        statuses = {item['id']: item['status'] for item in queue.list()}
        self.assertEqual(statuses[identity], 'unknown')
        self.assertEqual(statuses[waiting], 'held')
        self.assertIsNone(queue.claim('follow-up', 'job'))
        self.assertIsNotNone(queue.claim('follow-up', identity=waiting))

    def test_queue_idempotence_cancel_and_frozen_settings(self):
        queue = MessageQueue(self.home / 'queue.db')
        settings = {'model': 'original'}
        identity = queue.add('follow-up', 'job', 'session', 'later', settings)
        settings['model'] = 'changed'
        self.assertEqual(queue.add('follow-up', 'job', 'session', 'later', settings, identity), identity)
        with self.assertRaises(ValueError): queue.add('follow-up', 'job', 'session', 'different', settings, identity)
        self.assertEqual(queue.claim('follow-up', 'job')['settings']['model'], 'original')
        second = queue.add('steering', 'job', 'session', 'cancel', {})
        queue.edit(second, cancel=True)
        self.assertIsNone(queue.claim('steering', 'job'))

    def test_fork_copies_only_complete_messages_no_runtime_or_usage(self):
        source = self.session()
        source.add({'role': 'user', 'content': 'goal'})
        call = tool_call('read', {'path': 'a'})
        source.add({'role': 'assistant', 'content': None, 'tool_calls': [call]})
        before = source.path.read_bytes()
        with self.assertRaises(ValueError): fork_session(source, self.home, self.root)
        self.assertEqual(before, source.path.read_bytes())
        source.add({'role': 'tool', 'tool_call_id': call['id'], 'content': '{"ok":true}'})
        source.record_run({'run_id': 'r', 'verification_status': 'passed'})
        before = source.path.read_bytes()
        identity = fork_session(source, self.home, self.root)
        child = Session.load(source.path.parent / (identity + '.jsonl'), self.root)
        self.addCleanup(child.close)
        self.assertEqual(child.messages, source.messages)
        self.assertEqual(child.header['parent']['session_id'], source.header['id'])
        self.assertIsNone(child.last_run)
        self.assertEqual(child.usage['api_calls'], 0)
        self.assertEqual(before, source.path.read_bytes())

    def test_projects_do_not_mutate_running_workspace(self):
        primary = Workspace(self.root, self.home)
        primary.memory_key = 'private'
        primary.job = {'finished': False, 'id': 'running'}
        other = self.base / 'other'
        other.mkdir()
        projects = Projects(primary)
        identity = projects.add(str(other))['id']
        selected = projects.get(identity)
        self.assertEqual(primary.cwd, self.root.resolve())
        self.assertEqual(primary.job['id'], 'running')
        self.assertEqual(selected.cwd, other.resolve())
        self.assertEqual(selected.memory_key, '')
        selected.settings['model'] = 'different'
        self.assertNotEqual(primary.settings['model'], 'different')
        self.assertNotEqual(primary.inbox.path, selected.inbox.path)
        self.assertIn(identity, {p['id'] for p in Projects(primary).list()})

    def test_followup_dispatch_uses_snapshot_and_rebinds_remainder(self):
        workspace = Workspace(self.root, self.home)
        workspace.memory_key = 'old-key'
        workspace.job = {'id': 'old', 'session_id': 's', 'finished': False}
        workspace.jobs['old'] = workspace.job
        first = workspace.queue_message({'kind': 'follow-up', 'job_id': 'old', 'prompt': 'first'})['id']
        second = workspace.queue_message({'kind': 'follow-up', 'job_id': 'old', 'prompt': 'second'})['id']
        model = workspace.settings['model']
        workspace.settings['model'] = 'new-model'
        workspace.memory_key = 'new-key'
        workspace.job['finished'] = True
        with patch.object(workspace, 'start', return_value={'job_id': 'new'}) as start:
            workspace.dispatch_followup('old')
        self.assertEqual(start.call_args.kwargs['frozen_settings']['model'], model)
        self.assertEqual(start.call_args.kwargs['frozen_key'], 'old-key')
        statuses = {item['id']: item for item in workspace.inbox.list()}
        self.assertEqual(statuses[first]['status'], 'dispatched')
        self.assertEqual(statuses[second]['job_id'], 'new')

    def test_project_shutdown_cancels_all_and_refuses_new_dispatch(self):
        primary = Workspace(self.root, self.home)
        projects = Projects(primary)
        other = self.base / 'other'
        other.mkdir()
        child = projects.get(projects.add(str(other))['id'])
        with patch.object(primary, 'cancel') as first, patch.object(child, 'cancel') as second:
            projects.close()
            first.assert_called_once()
            second.assert_called_once()
        with self.assertRaisesRegex(ValueError, '关闭'):
            child.start({'prompt': 'must not run'})

    def test_after_hook_failure_does_not_reexecute_action(self):
        hooks = Hooks()
        calls = []
        hooks.on('tool.after', lambda event: (_ for _ in ()).throw(RuntimeError('observer')))
        with self.assertRaises(HookError):
            hooks.call('tool', {}, lambda: calls.append('once'))
        self.assertEqual(calls, ['once'])
