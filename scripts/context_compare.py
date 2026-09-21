"""Deterministic context regression, not a model-quality or token-cost benchmark."""
import json
import tempfile
from pathlib import Path

from deepblue.compaction import compact_context, message_bytes
from deepblue.config import Config
from deepblue.models import Completion
from deepblue.session import Session


class SummaryFixture:
    def __init__(self, style):
        self.style, self.calls = style, 0

    def complete(self, messages, tools, on_text=None):
        self.calls += 1
        state = dict(goals=["修复订单计算"], constraints=["不要修改测试，记住标记 BLUE-CONTEXT-42"],
                     changes=["已阅读 invoice.py"], open_questions=["尚未通过验证"], next_steps=["修复并测试"])
        text = json.dumps(state, ensure_ascii=False) if self.style == "structured" else "修复订单计算；不要修改测试；标记 BLUE-CONTEXT-42。已阅读 invoice.py，尚未通过验证，下一步修复并测试。"
        return Completion({"role": "assistant", "content": text}, "stop")


def main():
    report = {"kind": "scripted_regression_not_real_model_evaluation", "results": []}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for style in ("full", "text", "structured"):
            session = Session.create(root / style, root, "fixture", "system")
            try:
                session.add({"role": "user", "content": "修复订单计算。不要修改测试，记住标记 `BLUE-CONTEXT-42`。"})
                session.add({"role": "assistant", "content": "已阅读 invoice.py，尚未验证。" + "重复资料\n" * 800})
                for index in range(4):
                    session.add({"role": "user", "content": f"继续阶段 {index}"})
                    session.add({"role": "assistant", "content": "重复读取的历史资料\n" * 200})
                before = message_bytes(session.messages)
                client = SummaryFixture(style)
                if style != "full":
                    compact_context(session, client, Config(root, "fixture", summary_format=style), keep_turns=1)
                active = json.dumps(session.context_messages(), ensure_ascii=False)
                report["results"].append({"strategy": style, "before_bytes": before,
                    "after_bytes": message_bytes(session.context_messages()), "summary_calls": client.calls,
                    "marker_retained": "BLUE-CONTEXT-42" in active, "constraint_retained": "不要修改测试" in active,
                    "raw_messages": len(session.messages)})
            finally:
                session.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
