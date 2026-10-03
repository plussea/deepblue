from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_OUTPUT_BYTES = 32 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024


def bounded(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore") + "\n[输出已截断]"


@dataclass
class ToolContext:
    cwd: Path
    artifacts: Path
    shell_timeout: float = 120
    on_process: Callable[[dict], None] | None = None
    cancelled: Callable[[], bool] | None = None

    permission_mode: str = "trusted"
    protected_paths: tuple[Path, ...] = ()

    def path(self, path: str) -> Path:
        from ..permissions import check_path
        return check_path(self, path)


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list[str]
    execute: Callable[[ToolContext, dict], dict]

    def declaration(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties,
                           "required": self.required, "additionalProperties": False},
        }}

    def validate(self, args):
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象。")
        if set(args) - set(self.properties):
            raise ValueError("存在未知参数。")
        for key in self.required:
            if key not in args:
                raise ValueError(f"缺少参数：{key}")
        for key, value in args.items():
            schema = self.properties[key]
            kind = schema["type"]
            valid = ((kind == "string" and isinstance(value, str))
                     or (kind == "boolean" and type(value) is bool)
                     or (kind == "integer" and type(value) is int)
                     or (kind == "number" and type(value) in (int, float)))
            if not valid:
                raise ValueError(f"{key} 必须是 {kind}。")
            if kind in {"integer", "number"}:
                if value != value or abs(value) == float("inf"):
                    raise ValueError(f"{key} 必须是有限数值。")
                if "minimum" in schema and value < schema["minimum"]:
                    raise ValueError(f"{key} 不能小于 {schema['minimum']}。")
                if "maximum" in schema and value > schema["maximum"]:
                    raise ValueError(f"{key} 不能大于 {schema['maximum']}。")
            if kind == "string" and len(value) < schema.get("minLength", 0):
                raise ValueError(f"{key} 不能为空。")


class ToolRegistry:
    def __init__(self, context: ToolContext, tools: list[Tool]):
        from ..hooks import Hooks
        from ..permissions import authorize
        self.context = context
        self.hooks = Hooks()
        self.hooks.on("tool.before", lambda e: authorize(self.context, e["data"]["name"], e["data"]["arguments"]))
        self.tools = {tool.name: tool for tool in tools}

    def register(self, tool):
        if tool.name in self.tools:
            raise ValueError("工具名称已存在，禁止覆盖。")
        self.tools[tool.name] = tool

    def declarations(self):
        return [tool.declaration() for tool in self.tools.values()]

    def execute(self, name: str, arguments: str) -> dict:
        try:
            if name not in self.tools:
                raise ValueError(f"未知工具：{name}")
            tool = self.tools[name]
            args = json.loads(arguments)
            tool.validate(args)
            return self.hooks.call("tool", {"name": name, "arguments": args},
                                   lambda: tool.execute(self.context, args))
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "error": bounded(str(exc), 2000)}
