import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.agent import Agent
from deepblue.config import Config
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.verification import VerificationConfig, current_status
from deepblue.budget import BudgetClient, BudgetExceeded, run_budget
from deepblue.cli import parser
from test_agent import FakeClient, response, tool_call
from test_tools import python_command


class HarnessBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.config = Config(self.root, 'fake', home=self.base / 'home', max_steps=2)
        self.session = Session.create(self.config.home, self.root, 'fake', 'system')
        self.addCleanup(self.session.close)
        self.tools = create_tools(ToolContext(self.root, self.session.artifacts, 10))

    def agent(self, responses, check=True, cancelled=None):
        self.raw = FakeClient(responses)
        verification = VerificationConfig(python_command("from pathlib import Path; assert Path('answer').read_text() == 'good'"), self.root, 5, 0) if check else None
        return Agent(self.config, self.raw, self.tools, self.session, verification=verification, cancelled=cancelled)

    def write(self):
        result = response(tool_call('write', {'path': 'answer', 'content': 'good'}))
        result.usage = {'total_tokens': 10}
        return result

    def test_request_limit_checks_repair_without_claiming_completion_and_restores(self):
        self.config.max_requests = 1
        agent = self.agent([self.write()])
        result = agent.run('fix')
        self.assertEqual(result.status, 'budget_limit')
        self.assertEqual(result.verification_status, 'passed')
        self.assertFalse(result.successful)
        self.assertEqual(len(self.raw.requests), 1)
        record = self.session.last_run
        self.assertEqual(record['budget']['stop_reason'], 'call_budget')
        self.assertEqual(record['budget']['tokens'], 10)
        self.assertGreater(record['budget']['estimated_request_tokens'], 10)
        self.assertNotIn('deadline', record['budget'])
        self.assertEqual(record['budget']['requests'][0]['reported_tokens'], 10)
        self.session.close()
        restored = Session.load(self.session.path, self.root)
        self.addCleanup(restored.close)
        self.assertEqual(restored.last_run['verification_status'], 'passed')
        (self.root / 'answer').write_text('changed')
        self.assertEqual(current_status(restored.last_run, self.root, (self.config.home,)), 'stale')

    def test_step_limit_runs_final_check_and_failed_check_stays_failed(self):
        self.config.max_steps = 1
        result = self.agent([self.write()]).run('fix')
        self.assertEqual((result.status, result.verification_status), ('step_limit', 'passed'))
        (self.root / 'answer').write_text('bad')
        result = self.agent([response(tool_call('read', {'path': 'answer'}))]).run('fix')
        self.assertEqual((result.status, result.verification_status), ('step_limit', 'failed'))

    def test_truncated_response_never_executes_tool_but_checks_current_files(self):
        truncated = self.write()
        truncated.finish_reason = 'length'
        self.config.max_steps = 1
        result = self.agent([truncated]).run('fix')
        self.assertFalse((self.root / 'answer').exists())
        self.assertEqual(result.verification_status, 'failed')
        self.assertFalse(result.successful)

    def test_context_and_plain_output_limits_finalize(self):
        (self.root / 'answer').write_text('good')
        self.config.max_context_bytes = 1
        result = self.agent([]).run('fix')
        self.assertEqual((result.status, result.verification_status), ('context_limit', 'passed'))
        self.config.max_context_bytes = 400000
        result = self.agent([response(reason='length', content='partial')]).run('fix')
        self.assertEqual((result.status, result.verification_status), ('incomplete', 'passed'))

    def test_cancel_and_network_failure_do_not_start_checks(self):
        from deepblue.llm import ModelError
        for cancelled, responses in [(lambda: True, []), (None, [ModelError('offline')])]:
            with patch('deepblue.agent.verify') as check:
                result = self.agent(responses, cancelled=cancelled).run('fix')
                check.assert_not_called()
                self.assertIn(result.status, ('cancelled', 'error'))

    def test_keyboard_interrupt_during_final_check_is_cancelled(self):
        self.config.max_steps = 1
        with patch('deepblue.verification.shell', side_effect=KeyboardInterrupt):
            result = self.agent([self.write()]).run('fix')
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.verification_status, 'cancelled')

    def test_expired_time_skips_check_and_reserve_blocks_request(self):
        from deepblue.budget import run_budget as real_budget
        expired = real_budget(self.config, True)
        expired.update(deadline=0, request_deadline=0)
        with patch('deepblue.agent.run_budget', return_value=expired), patch('deepblue.agent.verify') as check:
            result = self.agent([]).run('fix')
            check.assert_not_called()
            self.assertEqual(result.status, 'budget_limit')
            self.assertEqual(self.session.last_run['finalization']['reason'], 'time_budget')
        (self.root / 'answer').write_text('good')
        reserved = real_budget(self.config, True)
        reserved['request_deadline'] = 0
        with patch('deepblue.agent.run_budget', return_value=reserved):
            result = self.agent([]).run('fix')
        self.assertEqual((result.status, result.verification_status), ('budget_limit', 'passed'))
        self.assertEqual(len(self.raw.requests), 0)

    def test_auto_compaction_shares_request_budget(self):
        self.config.max_requests = 1
        self.config.max_context_bytes = 6000
        self.config.compact_threshold = 0.2
        agent = self.agent([response(reason='stop', content='summary')], check=False)
        def compact(session, client, config, *args, **kwargs):
            client.complete([], [])
        with patch('deepblue.agent.compact_context', side_effect=compact):
            result = agent.run('x' * 1000)
        self.assertEqual(result.status, 'budget_limit')
        self.assertEqual(len(self.raw.requests), 1)
        self.assertEqual(self.session.last_run['budget']['calls'], 1)

    def test_retry_gets_new_run_budget_and_keeps_task_identity(self):
        self.config.max_requests = 1
        agent = self.agent([self.write(), response(reason='stop', content='done')], check=False)
        first = agent.run('fix')
        second = agent.run()
        self.assertEqual(first.status, 'budget_limit')
        self.assertEqual(second.status, 'completed')
        self.assertEqual(first.task_id, second.task_id)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(self.session.last_run['budget']['calls'], 1)

    def test_nested_rejection_is_not_counted_as_sent_request(self):
        inner_budget = run_budget(self.config)
        inner_budget['max_calls'] = 0
        raw = FakeClient([])
        inner = BudgetClient(raw, inner_budget, 20)
        outer_budget = run_budget(self.config)
        outer = BudgetClient(inner, outer_budget, 20)
        with self.assertRaises(BudgetExceeded):
            outer.complete([], [])
        self.assertEqual(outer_budget['calls'], 0)
        self.assertEqual(inner_budget['calls'], 0)
        self.assertEqual(outer.reason, 'call_budget')
        self.assertEqual(raw.requests, [])

    def test_budget_config_and_cli_validation(self):
        args = parser().parse_args(['--max-requests', '4', '--token-budget', '20000', '--run-seconds', '60'])
        self.assertEqual(args.max_requests, 4)
        for kwargs in ({'max_requests': True}, {'token_budget': 0}, {'run_seconds': float('nan')}, {'run_seconds': 5}):
            with self.assertRaises(ValueError):
                Config(self.root, 'fake', **kwargs)
        self.config.run_seconds = 60
        self.assertAlmostEqual(run_budget(self.config, True)['deadline'] - run_budget(self.config, True)['request_deadline'], 10, delta=0.1)
