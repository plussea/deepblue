from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Config:
    cwd: Path
    api_key: str = field(repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    provider: str = "deepseek"
    include_usage: bool = True
    token_parameter: str = "max_tokens"
    home: Path = field(default_factory=lambda: Path.home() / ".deepblue")
    permission_mode: str = "trusted"
    max_steps: int = 30
    active_checks: int = 2
    budget_estimator: str = "calibrated"
    max_requests: int | None = None
    token_budget: int | None = None
    run_seconds: float | None = None
    finalize_reserve_seconds: float = 10
    request_timeout: float = 120
    shell_timeout: float = 120
    max_tokens: int = 8192
    max_context_bytes: int = 400_000
    stream: bool = True
    auto_compact: bool = True
    compact_threshold: float = 0.8
    compact_keep_turns: int = 2
    summary_format: str = "structured"

    def __post_init__(self):
        if self.provider not in {"deepseek", "openai-compatible"}:
            raise ValueError("不支持的 API 类型。")
        if self.token_parameter not in {"max_tokens", "max_completion_tokens"} or type(self.include_usage) is not bool:
            raise ValueError("无效兼容参数。")
        from .permissions import MODES
        if self.permission_mode not in MODES:
            raise ValueError("无效权限模式。")
        if self.budget_estimator not in {"calibrated", "conservative"}:
            raise ValueError("无效的预算估计模式。")
        if type(self.active_checks) is not int or not 0 <= self.active_checks <= 10:
            raise ValueError('active_checks 必须是 0–10 的整数。')
        for name in ('max_requests', 'token_budget'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f'{name} 必须为正整数。')
        for name in ('run_seconds', 'finalize_reserve_seconds'):
            value = getattr(self, name)
            if value is not None and (type(value) not in (int, float) or value <= 0 or value == float('inf') or value != value):
                raise ValueError(f'{name} 必须为有限正数。')
        if self.finalize_reserve_seconds is None:
            raise ValueError('finalize_reserve_seconds 不能为空。')
        if self.run_seconds is not None and self.finalize_reserve_seconds >= self.run_seconds:
            raise ValueError('检查预留时间必须小于运行时间。')
        self.cwd = self.cwd.expanduser().resolve()
        self.home = self.home.expanduser().resolve()
        self.base_url = self.base_url.rstrip("/")
        if not 0.2 <= self.compact_threshold <= 0.95:
            raise ValueError("自动压缩阈值须为 0.2–0.95。")
        if not 1 <= self.compact_keep_turns <= 20 or self.summary_format not in {"structured", "text"}:
            raise ValueError("无效的压缩配置。")
        if not self.cwd.is_dir():
            raise ValueError(f"工作目录不存在：{self.cwd}")
        if not self.api_key.strip():
            raise ValueError("请先设置 DEEPSEEK_API_KEY 或 LLM_API_KEY 环境变量。")
        if not self.model.strip():
            raise ValueError("模型名称不能为空。")
        parsed = urlsplit(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API 地址不能包含凭据、查询参数或片段。")
        if not parsed.hostname or (parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )):
            raise ValueError("API 地址须为 HTTPS；仅本机测试允许 HTTP。")
        for name in ("max_steps", "request_timeout", "shell_timeout", "max_tokens", "max_context_bytes"):
            value = getattr(self, name)
            if value <= 0 or value == float("inf") or value != value:
                raise ValueError(f"{name} 必须为有限正数。")

    @property
    def completion_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"
