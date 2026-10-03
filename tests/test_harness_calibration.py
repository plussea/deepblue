import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.budget import BudgetClient, BudgetExceeded, InputEstimator, run_budget
from deepblue.config import Config
from deepblue.agent import Agent
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.verification import VerificationConfig, verify
from test_agent import FakeClient, response, tool_call
from test_tools import python_command


class CalibrationTests(unittest.TestCase):
    def test_calibrates_only_after_three_valid_samples_and_resets_missing_usage(self):
        estimator = InputEstimator()
        messages = [{'role': 'user', 'content': 'hello ' * 1000}]
        for _ in range(3):
            estimate = estimator.estimate(messages, [])
            self.assertEqual(estimate['estimator'], 'conservative')
            estimator.observe(estimate, {'prompt_tokens': 1500, 'total_tokens': 1510})
        estimate = estimator.estimate(messages, [])
        self.assertEqual(estimate['estimator'], 'calibrated')
        self.assertGreaterEqual(estimate['estimated_input_tokens'], 1875)
        self.assertLess(estimate['estimated_input_tokens'], estimate['input_bytes'])
        estimator.observe(estimate, {'total_tokens': 1510})
        self.assertEqual(estimator.estimate(messages, [])['estimator'], 'conservative')

    def test_schema_size_change_and_underestimate_fall_back(self):
        estimator = InputEstimator()
        messages = [{'role': 'user', 'content': 'x' * 1000}]
        for _ in range(3):
            estimator.observe(estimator.estimate(messages, []), {'prompt_tokens': 200, 'total_tokens': 210})
        self.assertEqual(estimator.estimate(messages, [{'name': 'new'}])['estimator'], 'conservative')
        self.assertEqual(estimator.estimate([{'role':'user','content':'x'*5000}], [])['estimator'], 'conservative')
        estimate = estimator.estimate(messages, [])
        details = estimator.observe(estimate, {'prompt_tokens': 900, 'total_tokens': 910})
        self.assertLess(details['input_estimation_error'], 0)
        self.assertEqual(estimator.estimate(messages, [])['estimator'], 'conservative')

    def test_calibrated_admission_keeps_output_reserve_and_total_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config(Path(temp), 'fake', max_requests=10, token_budget=20000)
            budget = run_budget(config)
            item = response(reason='stop', content='ok')
            item.usage = {'prompt_tokens': 1000, 'total_tokens': 1010}
            client = BudgetClient(FakeClient([item]*4), budget, 100)
            messages = [{'role': 'user', 'content': 'code ' * 1000}]
            for _ in range(3): client.complete(messages, [])
            budget['max_tokens'] = budget['tokens'] + 2200
            client.complete(messages, [])
            row = budget['requests'][-1]
            self.assertEqual(row['estimator'], 'calibrated')
            self.assertEqual(row['estimated_tokens'], row['estimated_input_tokens'] + 100 + 512)
            self.assertEqual(budget['max_tokens'], 5230)
            with self.assertRaises(BudgetExceeded): client.complete(messages, [])
            self.assertEqual(budget['calls'], 4)

    def test_conservative_mode_and_run_config_controls(self):
        from deepblue.cli import parser
        estimator = InputEstimator('conservative')
        messages = [{'role': 'user', 'content': 'x' * 1000}]
        for _ in range(4):
            estimate = estimator.estimate(messages, [])
            estimator.observe(estimate, {'prompt_tokens': 200, 'total_tokens': 210})
        self.assertEqual(estimator.estimate(messages, [])['estimator'], 'conservative')
        args = parser().parse_args(['--budget-estimator','conservative','--active-checks','0'])
        self.assertEqual((args.budget_estimator,args.active_checks), ('conservative',0))
        with tempfile.TemporaryDirectory() as root:
            for options in ({'active_checks':-1}, {'active_checks':True}, {'active_checks':11}, {'budget_estimator':'invalid'}):
                with self.assertRaises(ValueError): Config(Path(root),'fake',**options)

    def test_invalid_samples_never_train(self):
        e = InputEstimator()
        for value in (True, -1, 0, None, 1.2):
            estimate = e.estimate([], [])
            e.observe(estimate, {'prompt_tokens': value, 'total_tokens': 100})
        self.assertEqual(e.estimate([], [])['estimator'], 'conservative')


class ActiveVerificationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root = base / 'workspace'
        self.root.mkdir()
        self.config = Config(self.root, 'fake', home=base/'home', max_steps=8)
        self.session = Session.create(self.config.home, self.root, 'fake', 'system')
        self.addCleanup(self.session.close)
        self.tools = create_tools(ToolContext(self.root, self.session.artifacts, 10))
        self.verification = VerificationConfig(python_command("from pathlib import Path; assert Path('answer').read_text() == 'good'"), self.root, 10, 1)

    def write(self, content='good'):
        return response(tool_call('write', {'path':'answer','content':content}))

    def run_agent(self, items):
        self.client = FakeClient(items)
        return Agent(self.config, self.client, self.tools, self.session, verification=self.verification).run('fix')

    def test_change_checks_before_next_model_and_final_reuses(self):
        with patch('deepblue.agent.verify', wraps=verify) as check:
            result = self.run_agent([self.write(), response(reason='stop',content='done')])
        self.assertEqual(check.call_count, 1)
        self.assertEqual(result.verification_status, 'passed')
        self.assertIn('主动验收结果', self.client.requests[1][-1]['content'])
        self.assertEqual(self.client.requests[1][-2]['role'], 'tool')
        self.assertEqual(self.session.last_run['finalization']['status'], 'reused')

    def test_later_edit_invalidates_success_even_after_active_cap(self):
        self.config.active_checks = 1
        result = self.run_agent([self.write(), self.write('bad'), response(reason='stop'), response(reason='stop')])
        self.assertEqual([r['status'] for r in result.evidence], ['passed','failed'])
        self.assertEqual(result.verification_status, 'failed')
        self.assertFalse(result.successful)

    def test_failed_active_check_repairs_then_passes_without_duplicate_check(self):
        result = self.run_agent([self.write('bad'), self.write(), response(reason='stop')])
        self.assertEqual([r['status'] for r in result.evidence], ['failed','passed'])
        self.assertIn('程序化验收失败',self.client.requests[1][-1]['content'])
        self.assertTrue(result.successful)

    def test_shell_edits_are_detected(self):
        shell = response(tool_call('shell', {'command': python_command("from pathlib import Path; Path('answer').write_text('good')")}))
        result = self.run_agent([shell, response(reason='stop')])
        self.assertEqual(self.session.last_run['active_checks'], 1)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.verification_status, 'passed')

    def test_shell_without_file_changes_invalidates_cached_environment_evidence(self):
        items = [self.write(), response(tool_call('shell', {'command':python_command('pass')})), response(reason='stop')]
        result = self.run_agent(items)
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual(self.session.last_run['active_checks'], 1)

    def test_disable_and_noop_do_not_trigger_active_checks(self):
        (self.root/'answer').write_text('good')
        result = self.run_agent([self.write(), response(reason='stop')])
        self.assertEqual(self.session.last_run['active_checks'], 0)
        self.config.active_checks = 0
        result = self.run_agent([self.write('bad'), self.write(), response(reason='stop')])
        self.assertEqual(self.session.last_run['active_checks'], 0)
        self.assertEqual(len(result.evidence), 1)

    def test_active_checks_are_bounded_and_do_not_claim_completion_at_step_limit(self):
        self.config.max_steps = 3
        self.config.active_checks = 1
        result = self.run_agent([self.write('bad'), self.write('worse'), self.write()])
        self.assertEqual(self.session.last_run['active_checks'], 1)
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual((result.status,result.verification_status),('step_limit','passed'))
        self.assertFalse(result.successful)
