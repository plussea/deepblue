import json
import subprocess
import unittest
from unittest.mock import patch

from deepblue.isolation import diagnostics, probe
from deepblue.cli import main


class IsolationTests(unittest.TestCase):
    def test_missing_tools_never_report_isolation(self):
        with patch('deepblue.isolation.shutil.which', return_value=None):
            result = diagnostics()
        self.assertFalse(result['isolation_verified'])
        self.assertEqual(result['restricted_shell'], 'denied')
        self.assertEqual(result['backends']['docker']['status'], 'not_found')

    def test_docker_service_version_not_client_presence(self):
        with patch('deepblue.isolation.shutil.which', side_effect=lambda name: '/docker' if name == 'docker' else None), patch('deepblue.isolation.probe', return_value={'status': 'available', 'text': 'null'}):
            self.assertEqual(diagnostics()['backends']['docker']['status'], 'invalid_response')
        with patch('deepblue.isolation.shutil.which', side_effect=lambda name: '/docker' if name == 'docker' else None), patch('deepblue.isolation.probe', return_value={'status': 'available', 'text': json.dumps({'Version': 'example', 'Os': 'linux'})}):
            result = diagnostics()
            self.assertEqual(result['backends']['docker']['server_version'], 'example')
            self.assertFalse(result['isolation_verified'])

    def test_probe_timeout_and_error_do_not_echo_environment(self):
        with patch('deepblue.isolation.subprocess.run', side_effect=subprocess.TimeoutExpired(['tool'], 8)):
            self.assertEqual(probe(['tool']), {'status': 'timeout'})
        with patch('deepblue.isolation.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'secret', b'secret')):
            self.assertEqual(probe(['tool']), {'status': 'error', 'exit_code': 1})

    def test_windows_unicode_output(self):
        with patch('deepblue.isolation.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Ubuntu\r\n'.encode('utf-16-le'))):
            self.assertEqual(probe(['wsl'])['text'], 'Ubuntu\r\n')

    def test_cli_diagnostics_needs_no_key_or_task(self):
        with patch('deepblue.isolation.diagnostics', return_value={'isolation_verified': False}), patch('deepblue.cli.display') as output:
            self.assertEqual(main(['--check-isolation']), 0)
            self.assertFalse(json.loads(output.call_args.args[0])['isolation_verified'])
