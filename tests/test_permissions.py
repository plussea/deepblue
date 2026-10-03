import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.tools import ToolContext, create_tools
from deepblue.agent import Agent
from deepblue.config import Config
from deepblue.session import Session
from deepblue.verification import VerificationConfig
from deepblue.web import Workspace


class PermissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.context = ToolContext(self.root, self.base / 'logs', permission_mode='workspace')
        self.tools = create_tools(self.context)

    def call(self, name, **args):
        return self.tools.execute(name, json.dumps(args))

    def test_workspace_edits_and_escape_denials(self):
        self.assertTrue(self.call('write', path='new/a.txt', content='hello')['ok'])
        for path in ('../outside', str(self.base / 'outside'), '.git/config', '.deepblue/key', 'a.txt:stream'):
            self.assertFalse(self.call('write', path=path, content='bad')['ok'])
            self.assertFalse(self.call('read', path=path)['ok'])
        self.assertFalse((self.base / 'outside').exists())

    def test_read_only_and_shell_denied_without_process(self):
        (self.root / 'a').write_text('hello')
        self.context.permission_mode = 'read-only'
        self.assertTrue(self.call('read', path='a')['ok'])
        self.assertFalse(self.call('edit', path='a', old_text='hello', new_text='bad')['ok'])
        with patch('subprocess.Popen') as process:
            self.assertFalse(self.call('shell', command='echo bad')['ok'])
            process.assert_not_called()
        self.assertEqual((self.root / 'a').read_text(), 'hello')

    def test_search_cannot_read_protected_storage(self):
        private = self.root / 'private'
        private.mkdir()
        (private / 'secret.py').write_text('def secret(): pass')
        self.context.protected_paths = (private,)
        for name, args in [('read', {'path': 'private/secret.py'}), ('symbols', {'path': 'private'}), ('find', {'path': '..', 'pattern': '*'}), ('project_checks', {'path': '..'})]:
            self.assertFalse(self.call(name, **args)['ok'])
        self.assertEqual(self.call('grep', pattern='secret')['matches'], [])
        self.assertEqual(self.call('symbols')['symbols'], [])

    def test_hard_link_denied(self):
        import os
        source = self.base / 'outside'
        source.write_text('private')
        os.link(source, self.root / 'link')
        self.assertFalse(self.call('read', path='link')['ok'])
        self.assertFalse(self.call('write', path='link', content='bad')['ok'])
        self.assertEqual(source.read_text(), 'private')

    def test_trusted_preserves_legacy_access_and_invalid_mode_rejected(self):
        self.context.permission_mode = 'trusted'
        self.assertTrue(self.call('write', path='../legacy', content='ok')['ok'])
        with self.assertRaises(ValueError):
            Config(self.root, 'fake', permission_mode='unknown')

    def test_verification_cannot_bypass_policy_and_web_freezes_mode(self):
        config = Config(self.root, 'fake', home=self.base / 'home', permission_mode='workspace')
        session = Session.create(config.home, self.root, 'fake', 'system')
        self.addCleanup(session.close)
        with self.assertRaisesRegex(ValueError, '受限模式'):
            Agent(config, None, self.tools, session, verification=VerificationConfig('echo bad', self.root))
        workspace = Workspace(self.root, config.home, permission_mode='read-only')
        self.assertEqual(workspace.settings['permission_mode'], 'read-only')
