import sys
import tempfile
import time
import unittest
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_project import BudgetClient, failure, hashes, independent, prepare, snapshot, summary
from project_tasks import TASKS
from deepblue.llm import ModelError
from deepblue.models import Completion
import evaluate_project


class CountingClient:
    def __init__(self, usage=None, fail=False):
        self.calls = 0
        self.usage = {'total_tokens': 10} if usage is None else usage
        self.fail = fail

    def complete(self, *args):
        self.calls += 1
        if self.fail:
            raise ModelError('DeepSeek HTTP 401')
        return Completion({'role': 'assistant', 'content': 'ok'}, 'stop', self.usage)


class ProjectEvaluationTests(unittest.TestCase):
    def test_navigation_ablation_removes_only_navigation_tools(self):
        from deepblue.tools import ToolContext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = ToolContext(root, root / 'artifacts')
            enabled = evaluate_project.evaluation_tools(ctx, 'on')
            disabled = evaluate_project.evaluation_tools(ctx, 'off')
            self.assertEqual(set(enabled.tools) - set(disabled.tools), {'symbols', 'project_checks'})
            self.assertEqual(set(disabled.tools), {'read', 'write', 'edit', 'shell', 'find', 'grep'})
            self.assertFalse(disabled.execute('symbols', '{}')['ok'])
            self.assertEqual(len(enabled.declarations()), len(disabled.declarations()) + 2)

    def test_navigation_summary_distinguishes_missing_and_no_file_change(self):
        rows = [{}, {'tool_metrics': {}}, {'tool_metrics': dict(tool_calls=3, repeated_reads=1,
                 calls_before_first_file_change=0)}, {'tool_metrics': dict(tool_calls=5,
                 calls_before_first_file_change=None)}]
        result = evaluate_project.navigation_summary(rows)
        self.assertEqual(result['measured_runs'], 2)
        self.assertEqual(result['totals']['tool_calls'], 8)
        self.assertEqual(result['totals']['repeated_reads'], 1)
        self.assertEqual(result['first_change_runs'], 1)
        self.assertEqual(result['mean_calls_before_first_file_change'], 0)
        self.assertIsNone(evaluate_project.navigation_summary([])['mean_calls_before_first_file_change'])

    def test_paired_schedule_reverses_order_and_keeps_budget_denominator(self):
        from navigation_tasks import TASKS as tasks
        runs = list(evaluate_project.schedule(tasks, 'paired', 2))
        self.assertEqual(len(runs), 16)
        self.assertEqual(len({(t['id'], m, n, r) for t, m, n, r in runs}), 16)
        first = [n for t, m, n, r in runs if t == tasks[0] and m == 'baseline' and r == 1]
        second = [n for t, m, n, r in runs if t == tasks[0] and m == 'baseline' and r == 2]
        self.assertEqual(first, second[::-1])
        rows = [dict(mode=m, navigation=n, split=t['split'], execution_status='not_run',
                     independent_status='not_run', failure_category='cancelled', usage={})
                for t, m, n, r in runs]
        groups = summary(rows)
        self.assertEqual(len(groups), 4)
        self.assertTrue(all(g['planned_runs'] == 4 and g['not_run'] == 4 for g in groups.values()))
        budget = self.budget(); budget['max_calls'] = 128
        self.assertEqual(evaluate_project.allocate(budget, len(runs), 120)['max_calls'], 8)

    def test_navigation_fixtures(self):
        from navigation_tasks import TASKS as tasks
        sources = snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            for task in tasks:
                for broken in (False, True):
                    root = Path(temporary) / (task['id'] + str(broken))
                    prepare(task, root, sources, broken)
                    self.assertEqual(independent(task, root)['status'], 'failed' if broken else 'passed')

    def budget(self, **extra):
        return dict(calls=0, tokens=0, max_calls=2, max_tokens=10000,
                    deadline=time.monotonic()+30, unknown_usage=False, **extra)

    def test_frozen_sources_fail_mutations_pass_reference_and_match_across_modes(self):
        sources = snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for task in TASKS:
                with self.subTest(task=task['id']):
                    reference = base / (task['id']+'-reference')
                    broken = base / (task['id']+'-broken')
                    twin = base / (task['id']+'-twin')
                    prepare(task, reference, sources, False)
                    prepare(task, broken, sources)
                    prepare(task, twin, sources)
                    self.assertEqual(hashes(broken), hashes(twin))
                    self.assertEqual(independent(task, reference)['status'], 'passed')
                    self.assertEqual(independent(task, broken)['status'], 'failed')

    def test_call_limit_includes_failed_requests_and_blocks_before_send(self):
        b = self.budget(); raw = CountingClient(fail=True); client = BudgetClient(raw, b, 20)
        for _ in range(3):
            with self.assertRaises(ModelError):
                client.complete([], [])
        self.assertEqual(raw.calls, 2)
        self.assertEqual(b['calls'], 2)
        self.assertEqual(client.reason, 'call_budget')

    def test_local_budget_does_not_consume_next_mode_allocation(self):
        b=self.budget(); b['max_calls']=8; b['max_tokens']=10000
        first=evaluate_project.allocate(b,2,10)
        first['tokens']=first['max_tokens']
        raw=CountingClient(); client=BudgetClient(raw,b,20,first)
        with self.assertRaises(ModelError): client.complete([],[])
        self.assertEqual(client.scope,'run'); self.assertEqual(b['calls'],0)
        second=evaluate_project.allocate(b,2,10)
        self.assertEqual(second['tokens'],0)
        self.assertEqual(second['max_tokens'],first['max_tokens'])
        BudgetClient(raw,b,20,second).complete([],[])
        self.assertEqual(second['calls'],1); self.assertEqual(b['calls'],1)
        self.assertEqual(second['tokens'],10); self.assertEqual(b['tokens'],10)

    def test_token_reservation_time_and_missing_usage_stop_further_calls(self):
        for field, value, reason in [('max_tokens', 1, 'token_budget'), ('deadline', 0, 'time_budget')]:
            b=self.budget(); b[field]=value; raw=CountingClient(); client=BudgetClient(raw,b,20)
            with self.assertRaises(ModelError): client.complete([], [])
            self.assertEqual(raw.calls,0); self.assertEqual(client.reason,reason)
        b=self.budget(); raw=CountingClient(usage={}); client=BudgetClient(raw,b,20)
        client.complete([], [])
        with self.assertRaises(ModelError): client.complete([], [])
        self.assertEqual(raw.calls,1); self.assertEqual(client.reason,'usage_unavailable')

    def test_summary_keeps_skips_failures_and_splits_in_denominator(self):
        rows=[dict(mode='baseline',split='dev',execution_status='finished',independent_status='passed',failure_category=None,usage={'total_tokens':10}),
              dict(mode='baseline',split='holdout',execution_status='finished',independent_status='failed',failure_category='independent_check_failed',usage={'total_tokens':20}),
              dict(mode='baseline',split='holdout',execution_status='not_run',independent_status='not_run',failure_category='token_budget',usage={})]
        report=summary(rows)['baseline']
        self.assertEqual(report['runs'],3); self.assertEqual(report['successes'],1)
        self.assertEqual(report['started_runs'],2); self.assertEqual(report['not_run'],1)
        self.assertEqual(report['false_finished_rate'],.5)
        self.assertEqual(report['splits']['holdout']['runs'],2)
        self.assertEqual(report['failure_categories']['token_budget'],1)
        self.assertEqual(failure(dict(independent_status='passed',integrity_violations=['check.py']),[]),'scope_or_check_modified')
        self.assertEqual(failure(dict(independent_status='failed',execution_status='incomplete'),[]),'output_truncated')
        interrupted=summary([dict(mode='verified',split='dev',execution_status='error',independent_status='passed',failure_category='token_budget',usage={})])['verified']
        self.assertEqual(interrupted['successes'],1)
        self.assertEqual(interrupted['finished_successes'],0)
        self.assertEqual(interrupted['passed_but_interrupted'],1)

    def test_runner_executes_both_modes_with_independent_evidence_without_api(self):
        task=TASKS[0]; source=snapshot()[task['file']].decode('utf-8')
        class ReferenceClient:
            def __init__(self, *args, **kwargs): self.sent=False
            def complete(self, *args):
                if not self.sent:
                    self.sent=True
                    return Completion({'role':'assistant','content':None,'tool_calls':[
                        {'id':'repair','type':'function','function':{'name':'write','arguments':json.dumps({'path':task['file'],'content':source})}}
                    ]},'tool_calls',{'total_tokens':10})
                return Completion({'role':'assistant','content':'done'},'stop',{'total_tokens':10})
        with tempfile.TemporaryDirectory() as temporary, patch.object(evaluate_project,'TASKS',[task]), \
             patch.object(evaluate_project,'DeepSeekClient',ReferenceClient), \
             patch.dict(os.environ,{'DEEPSEEK_API_KEY':'not-a-real-evaluation-key'}), \
             patch.object(sys,'argv',['evaluate_project.py','--live','--output',temporary]), redirect_stdout(io.StringIO()):
            self.assertEqual(evaluate_project.main(),0)
            report=json.loads(next(Path(temporary).glob('*/report.json')).read_text(encoding='utf-8'))
            self.assertTrue(report['complete']); self.assertEqual(report['planned_runs'],2)
            for mode in ('baseline','verified'):
                self.assertEqual(report['summary'][mode]['successes'],1)
            self.assertEqual(report['records'][0]['initial_sha256'],report['records'][1]['initial_sha256'])
            self.assertEqual(report['records'][1]['verification_status'],'passed')
            self.assertNotIn('not-a-real-evaluation-key',json.dumps(report))
            limited=str(Path(temporary)/'limited')
            with patch.object(sys,'argv',['evaluate_project.py','--live','--max-calls','2','--output',limited]):
                self.assertEqual(evaluate_project.main(),0)
            limited_report=json.loads(next(Path(limited).glob('*/report.json')).read_text(encoding='utf-8'))
            self.assertTrue(limited_report['all_runs_started'])
            self.assertFalse(limited_report['all_runs_finished'])
            self.assertEqual(limited_report['budget']['calls'],2)
            self.assertEqual([r['budget_scope'] for r in limited_report['records']],['run','run'])
