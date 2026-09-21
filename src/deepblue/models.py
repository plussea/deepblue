from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

Message = dict[str, Any]
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class Completion:
    message: Message
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[dict],
                 on_text: Callable[[str], None] | None = None) -> Completion: ...


@dataclass
class RunResult:
    status: str
    steps: int
    verification_status: str = "unverified"
    evidence: list[dict] = field(default_factory=list)
    task_id: str = ""
    run_id: str = ""

    @property
    def execution_status(self) -> str:
        return "finished" if self.status == "completed" else self.status

    @property
    def successful(self) -> bool:
        return self.status == "completed" and self.verification_status in {"passed", "unverified", "not_applicable"}
