import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepblue.agent import Agent
from deepblue.cli import parser
from deepblue.config import Config
from deepblue.evaluation import TASKS, independent_check, prepare, summarize
from deepblue.session import Session
from deepblue.tools import ToolContext, create_tools
from deepblue.verification import VerificationConfig, current_status, fingerprint, verify
from test_agent import FakeClient, response, tool_call
from test_tools import python_command


class VerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.config = Config(self.root, "fake", home=self.base / "home", max_steps=5)
        self.session = Session.create(self.config.home, self.root, "fake", "system")
        self.addCleanup(self.session.close)
        self.tools = create_tools(ToolContext(self.root, self.session.artifacts, 10))

    def agent(self, command, responses, repairs=1):
        client = FakeClient(responses)
        verification = VerificationConfig(command, self.root, 10, repairs)
        return Agent(self.config, client, self.tools, self.session, verification=verification), client

    def test_false_completion_is_failed(self):
        agent, _ = self.agent(python_command("raise AssertionError('not done')"),
                              [response(reason="stop", content="完成")], 0)
        result = agent.run("fix")
        self.assertEqual(result.execution_status, "finished")
        self.assertEqual(result.verification_status, "failed")
        self.assertFalse(result.successful)
        self.assertTrue(Path(result.evidence[0]["log_path"]).exists())
        self.assertEqual(self.session.last_run["run_id"], result.run_id)

    def test_repair_then_verify_and_restore(self):
        (self.root / "answer").write_text("bad")
        command = python_command("from pathlib import Path; assert Path('answer').read_text() == 'good'")
        agent, client = self.agent(command, [response(reason="stop", content="done"),
            response(tool_call("write", {"path": "answer", "content": "good"})),
            response(reason="stop", content="fixed")])
        result = agent.run("fix")
        self.assertEqual(result.steps, 3)
        self.assertEqual([r["status"] for r in result.evidence], ["failed", "passed"])
        self.assertIn("程序化验收失败", client.requests[1][-1]["content"])
        self.assertTrue(result.successful)
        self.session.close()
        restored = Session.load(self.session.path, self.root)
        self.addCleanup(restored.close)
        self.assertEqual(current_status(restored.last_run, self.root, (self.config.home,)), "passed")
        (self.root / "answer").write_text("changed")
        self.assertEqual(current_status(restored.last_run, self.root, (self.config.home,)), "stale")

    def test_shared_step_budget_and_repair_limit(self):
        self.config.max_steps = 1
        agent, client = self.agent(python_command("raise AssertionError()"), [response(reason="stop")], 10)
        result = agent.run("fix")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(result.verification_status, "failed")
        self.config.max_steps = 5
        agent, client = self.agent(python_command("raise AssertionError()"), [response(reason="stop"), response(reason="stop")])
        result = agent.run("fix")
        self.assertEqual(result.steps, 2)
        self.assertEqual(len(result.evidence), 2)

    def test_no_checks_unverified_and_next_run_invalidates_previous(self):
        agent, _ = self.agent(python_command("pass"), [response(reason="stop")])
        self.assertEqual(agent.run("fix").verification_status, "passed")
        plain = Agent(self.config, FakeClient([response(reason="stop")]), self.tools, self.session)
        self.assertEqual(plain.run("explain").verification_status, "unverified")
        self.assertEqual(self.session.last_run["verification_status"], "unverified")

    def check(self, command, timeout=10):
        return verify(VerificationConfig(command, self.root, timeout), self.root,
                      self.session.artifacts, (self.config.home,), "task", "run")

    def test_timeout_and_startup_error_are_not_assertion_failures(self):
        result = self.check(python_command("import time; time.sleep(10)"), 0.2)
        self.assertEqual(result["status"], "error")
        self.assertIn("超时", result["error"])
        with patch("deepblue.verification.shell", side_effect=OSError("startup unavailable")):
            self.assertEqual(self.check("whatever")["status"], "error")

    def test_cancelled_check_persists_evidence(self):
        with patch("deepblue.verification.shell", side_effect=KeyboardInterrupt):
            result = self.check("whatever")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))["status"], "cancelled")

    def test_mutating_check_cannot_claim_current_pass(self):
        result = self.check(python_command("from pathlib import Path; Path('new').write_text('changed')"))
        self.assertEqual(result["status"], "stale")

    def test_fingerprint_includes_untracked_content_and_excludes_cache(self):
        original = fingerprint(self.root)
        (self.root / "new.py").write_text("a")
        changed = fingerprint(self.root)
        self.assertNotEqual(original["sha256"], changed["sha256"])
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "file").write_text("ignored")
        self.assertEqual(changed["sha256"], fingerprint(self.root)["sha256"])
        (self.root / "new.py").write_text("b")
        self.assertNotEqual(changed["sha256"], fingerprint(self.root)["sha256"])

    def test_config_validation(self):
        for timeout in (float("nan"), float("inf"), 0):
            with self.assertRaises(ValueError):
                VerificationConfig("echo ok", self.root, timeout)
        with self.assertRaises(ValueError):
            VerificationConfig(" ", self.root)
        args = parser().parse_args(["-p", "fix", "--verify", "python check.py", "--verify-repairs", "0"])
        self.assertEqual(args.verify_repairs, 0)

    def test_explicit_explanation_task_is_not_applicable(self):
        agent = Agent(self.config, FakeClient([response(reason="stop")]), self.tools, self.session,
                      verification_not_applicable="纯解释")
        result = agent.run("解释代码")
        self.assertEqual(result.verification_status, "not_applicable")
        self.assertTrue(result.successful)

    def test_fingerprint_failure_prevents_command(self):
        with patch("deepblue.verification.fingerprint", side_effect=ValueError("oversize")), patch("deepblue.verification.shell") as execute:
            result = self.check("whatever")
        execute.assert_not_called()
        self.assertEqual(result["status"], "error")


class EvaluationTests(unittest.TestCase):
    def test_five_fixtures_fail_initially_and_pass_reference_solutions(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for task in TASKS:
                with self.subTest(task=task["id"]):
                    root = base / task["id"]
                    prepare(task, root)
                    self.assertFalse(independent_check(task, root, base / "logs")["ok"])
                    for name, content in task["solution"].items():
                        (root / name).write_text(content, encoding="utf-8")
                    self.assertTrue(independent_check(task, root, base / "logs")["ok"])

    def test_metrics_count_failures_and_zero_denominators(self):
        self.assertIsNone(summarize([])["tokens_per_success"])
        report = summarize([
            {"execution_status": "finished", "independent_status": "failed", "usage": {"total_tokens": 10}},
            {"execution_status": "finished", "independent_status": "passed", "usage": {"total_tokens": 20}},
            {"execution_status": "error", "independent_status": "error", "usage": {"total_tokens": 5}},
        ])
        self.assertEqual(report["success_rate"], 1 / 3)
        self.assertEqual(report["false_finished_rate"], 0.5)
        self.assertEqual(report["tokens_per_success"], 35)
