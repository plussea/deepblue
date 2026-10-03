"""Explicit, trusted Python extensions. No workspace module discovery."""
from copy import deepcopy
from .permissions import PolicyDenied

EVENTS = frozenset(f'{stage}.{phase}' for stage in ('run', 'tool', 'compact', 'verification')
                   for phase in ('before', 'after', 'error'))


class HookError(ValueError):
    pass


class Hooks:
    version = 1

    def __init__(self):
        self._handlers = {}

    def on(self, event, handler, *, version=1):
        if version != self.version or event not in EVENTS or not callable(handler):
            raise ValueError('不支持的 hook 版本、事件或回调。')
        self._handlers.setdefault(event, []).append(handler)
        return lambda: self._handlers[event].remove(handler)

    def emit(self, event, data):
        if event not in EVENTS:
            raise ValueError('未知 hook 事件。')
        for handler in tuple(self._handlers.get(event, ())):
            try:
                handler({'version': 1, 'event': event, 'data': deepcopy(data)})
            except (PolicyDenied, HookError):
                raise
            except Exception as exc:
                # Do not expose secrets from arbitrary extension exception text.
                raise HookError(f'hook {event} 失败（{type(exc).__name__}）；没有自动重试。') from exc

    def call(self, stage, data, action):
        try:
            self.emit(stage + '.before', data)
            result = action()
            self.emit(stage + '.after', {**data, 'result': result})
            return result
        except BaseException as exc:
            try:
                self.emit(stage + '.error', {**data, 'error_type': type(exc).__name__})
            except Exception:
                pass  # Preserve the original failure, never invoke action again.
            raise
