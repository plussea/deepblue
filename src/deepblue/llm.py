from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Config
from .models import Completion, Message


class ModelError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    # Never forward the Authorization header to a redirect target.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekClient:
    def __init__(self, config: Config):
        self.config = config
        self.opener = build_opener(NoRedirect())

    def complete(self, messages: list[Message], tools: list[dict]) -> Completion:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "thinking": {"type": "disabled"},
            "stream": False,
            "max_tokens": self.config.max_tokens,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.config.base_url + "/chat/completions", data=data,
            headers={"Authorization": "Bearer " + self.config.api_key,
                     "Content-Type": "application/json", "User-Agent": "deepblue/0.1.0"},
        )
        try:
            with self.opener.open(request, timeout=self.config.request_timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ModelError("模型响应过大，已停止。")
            result = json.loads(raw)
        except HTTPError as exc:
            hints = {400: "请求被拒绝；请检查模型、参数或上下文长度。",
                     401: "API Key 无效。", 402: "账户余额不足。",
                     403: "接口访问被拒绝。", 404: "API 地址或模型不存在。",
                     429: "请求受限，请稍后重试。"}
            # Do not print raw responses: upstream errors can echo credentials/prompts.
            raise ModelError(f"DeepSeek HTTP {exc.code}：" + hints.get(exc.code, "服务请求失败，请稍后重试。")) from None
        except (URLError, socket.timeout, OSError):
            raise ModelError("无法连接 DeepSeek 或请求超时，请检查网络和 API 地址。") from None
        except (ValueError, UnicodeError):
            raise ModelError("DeepSeek 返回了无效 JSON。") from None
        return self._parse(result)

    @staticmethod
    def _parse(result) -> Completion:
        try:
            choice = result["choices"][0]
            source = choice["message"]
            reason = choice["finish_reason"]
            if source["role"] != "assistant" or not isinstance(reason, str):
                raise ValueError()
            content = source.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError()
            message = {"role": "assistant", "content": content}
            calls = source.get("tool_calls") or []
            if not isinstance(calls, list):
                raise ValueError()
            ids = set()
            for call in calls:
                function = call["function"]
                if (call["type"] != "function" or not isinstance(call["id"], str)
                    or not call["id"] or call["id"] in ids
                    or not isinstance(function["name"], str)
                    or not isinstance(function["arguments"], str)):
                    raise ValueError()
                ids.add(call["id"])
            if calls:
                message["tool_calls"] = calls
            if not calls and not content:
                raise ValueError()
            return Completion(message, reason, result.get("usage") or {})
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelError("DeepSeek 返回了不完整或无效的消息，未执行任何工具。") from None
