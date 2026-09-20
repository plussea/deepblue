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
    def complete(self, messages: list[Message], tools: list[dict]) -> Completion: ...


@dataclass
class RunResult:
    status: str
    steps: int
