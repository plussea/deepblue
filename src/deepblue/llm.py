from __future__ import annotations

import json
import socket
from http.client import HTTPException
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Config
from . import __version__
from .models import Completion, Message


class ModelError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    # Never forward the Authorization header to a redirect target.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleClient:
    def __init__(self, config: Config, cancelled=None):
        self.config = config
        self.opener = build_opener(NoRedirect())
        self.cancelled = cancelled or (lambda: False)

    def complete(self, messages: list[Message], tools: list[dict],
                 on_text: Callable[[str], None] | None = None) -> Completion:
        if self.cancelled():
            raise KeyboardInterrupt()
        streaming = self.config.stream and on_text is not None
        payload = {
            "model": self.config.model,
            "messages": messages,
            "stream": streaming,
            self.config.token_parameter: self.config.max_tokens,
        }
        if self.config.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if streaming and self.config.include_usage:
            payload["stream_options"] = {"include_usage": True}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.config.completion_url, data=data,
            headers={"Authorization": "Bearer " + self.config.api_key,
                     "Content-Type": "application/json", "User-Agent": "deepblue/" + __version__},
        )
        try:
            with self.opener.open(request, timeout=self.config.request_timeout) as response:
                if streaming:
                    return self._read_stream(response, on_text, self.cancelled)
                chunks, size = [], 0
                while size <= 16 * 1024 * 1024:
                    if self.cancelled():
                        raise KeyboardInterrupt()
                    chunk = response.read1(min(65536, 16 * 1024 * 1024 + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                raw = b"".join(chunks)
            if len(raw) > 16 * 1024 * 1024:
                raise ModelError("模型响应过大，已停止。")
            result = json.loads(raw)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            hints = {400: "请求被拒绝；请检查模型、参数或上下文长度。",
                     401: "API Key 无效。", 402: "账户余额不足。",
                     403: "接口访问被拒绝。", 404: "API 地址或模型不存在。",
                     429: "请求受限，请稍后重试。"}
            # Do not print raw responses: upstream errors can echo credentials/prompts.
            raise ModelError(f"模型 API HTTP {status}：" + hints.get(status, "服务请求失败，请稍后重试。")) from None
        except (URLError, socket.timeout, OSError, HTTPException):
            raise ModelError("无法连接模型 API 或请求超时，请检查网络和 API 地址。") from None
        except (ValueError, UnicodeError):
            raise ModelError("模型 API 返回了无效 JSON。") from None
        return self._parse(result)

    @staticmethod
    def _events(response, cancelled=None):
        """Decode bounded SSE events, including comments and multiline data."""
        parts = []
        size = 0
        while True:
            if cancelled and cancelled():
                raise KeyboardInterrupt()
            raw = response.readline(1024 * 1024 + 1)
            if cancelled and cancelled():
                raise KeyboardInterrupt()
            if not raw:
                raise ModelError("流式连接在 [DONE] 前结束，未执行本次工具调用。")
            size += len(raw)
            if len(raw) > 1024 * 1024 or size > 16 * 1024 * 1024:
                raise ModelError("流式响应过大，已停止。")
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if parts:
                    data = "\n".join(parts)
                    parts = []
                    yield data
                    if data == "[DONE]":
                        return
            elif line.startswith("data:"):
                parts.append(line[5:].removeprefix(" "))

    @classmethod
    def _read_stream(cls, response, on_text, cancelled=None) -> Completion:
        content = []
        calls = {}
        reason = None
        usage = {}
        try:
            for event in cls._events(response, cancelled):
                if event == "[DONE]":
                    break
                chunk = json.loads(event)
                if "error" in chunk:
                    raise ModelError("模型 API 返回流式错误，未执行本次工具调用。")
                if chunk.get("usage") is not None:
                    if not isinstance(chunk["usage"], dict):
                        raise ValueError()
                    usage = chunk["usage"]
                choices = chunk["choices"]
                if not choices:
                    continue  # Some endpoints send a separate usage-only chunk.
                if len(choices) != 1 or choices[0].get("index", 0) != 0:
                    raise ValueError()
                choice = choices[0]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                fragments = delta.get("tool_calls") or []
                if reason is not None and (text or fragments):
                    raise ValueError()
                if delta.get("role", "assistant") != "assistant":
                    raise ValueError()
                if text is not None:
                    if not isinstance(text, str):
                        raise ValueError()
                    content.append(text)
                    if text:
                        on_text(text)
                for fragment in fragments:
                    index = fragment["index"]
                    if type(index) is not int or not 0 <= index < 128:
                        raise ValueError()
                    call = calls.setdefault(index, {"id": "", "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                    if "id" in fragment:
                        if call["id"] and call["id"] != fragment["id"]:
                            raise ValueError()
                        call["id"] = fragment["id"]
                    if fragment.get("type", "function") != "function":
                        raise ValueError()
                    function = fragment.get("function") or {}
                    for key in ("name", "arguments"):
                        value = function.get(key)
                        if value is not None:
                            if not isinstance(value, str):
                                raise ValueError()
                            call["function"][key] += value
                finish = choice.get("finish_reason")
                if finish is not None:
                    if not isinstance(finish, str) or (reason is not None and reason != finish):
                        raise ValueError()
                    reason = finish
            if reason is None or sorted(calls) != list(range(len(calls))):
                raise ValueError()
            message = {"role": "assistant", "content": "".join(content) or None}
            if calls:
                message["tool_calls"] = [calls[index] for index in sorted(calls)]
            return cls._parse({"choices": [{"message": message, "finish_reason": reason}], "usage": usage})
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ModelError("DeepSeek 流式响应不完整或格式无效，未执行本次工具调用。") from None

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
            raise ModelError("模型 API 返回了不完整或无效的消息，未执行任何工具。") from None


# Backwards-compatible import for existing callers.
DeepSeekClient = OpenAICompatibleClient
