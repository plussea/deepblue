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
    home: Path = field(default_factory=lambda: Path.home() / ".deepblue")
    max_steps: int = 30
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
