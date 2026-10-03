"""Shared request admission and usage accounting; estimates are not billing limits."""
from __future__ import annotations

import hashlib
import math
import json
import time

from .llm import ModelError


def run_budget(config, verification=False):
    deadline = time.monotonic() + config.run_seconds if config.run_seconds is not None else float('inf')
    return dict(calls=0, tokens=0, estimator_policy=config.budget_estimator, max_calls=config.max_requests or config.max_steps + 4,
                max_tokens=config.token_budget, unknown_usage=False, deadline=deadline,
                run_seconds=config.run_seconds, finalize_reserve_seconds=config.finalize_reserve_seconds if verification else 0,
                request_deadline=deadline - (config.finalize_reserve_seconds if verification else 0))


def snapshot(budget):
    # Monotonic deadlines are process-local, never persist as recoverable timestamps.
    return {k: v for k, v in budget.items() if k not in ('deadline', 'request_deadline')}


def record_request(budget, estimate, actual, outcome, details=None):
    rows = budget.setdefault('requests', [])
    rows.append({'attempt': budget['calls'], 'estimated_tokens': estimate,
                 'reported_tokens': actual, 'outcome': outcome, **(details or {})})
    if len(rows) > 100:
        del rows[:-100]
        budget['request_history_truncated'] = True


class InputEstimator:
    """Run-local empirical estimate, separated by tool schema; never a hard bound."""
    def __init__(self, mode="calibrated"):
        self.mode = mode
        self.samples = {}
        self.disabled = set()

    def estimate(self, messages, tools):
        encoded = json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=False).encode('utf-8')
        size = len(encoded)
        key = hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        samples = self.samples.get(key, [])
        # Avoid extrapolation after compaction or a large new file/tool result.
        comparable = [tokens / length for length, tokens in samples if 0.5 <= size / length <= 2]
        calibrated = self.mode == 'calibrated' and key not in self.disabled and len(comparable) >= 3
        predicted = max(math.ceil(size * max(comparable) * 1.25), math.ceil(size / 4)) if calibrated else size
        return dict(input_bytes=size, estimated_input_tokens=predicted,
                    estimator='calibrated' if calibrated else 'conservative', schema_key=key)

    def observe(self, estimate, usage):
        prompt = usage.get('prompt_tokens')
        total = usage.get('total_tokens')
        key = estimate['schema_key']
        valid = type(prompt) is int and prompt > 0 and type(total) is int and total >= prompt
        if not valid:
            self.samples.pop(key, None)
            return {'reported_prompt_tokens': None, 'input_estimation_error': None}
        error = estimate['estimated_input_tokens'] - prompt
        if error < 0:
            # An underestimated request disables calibration for this schema until a new run.
            self.disabled.add(key)
        rows = self.samples.setdefault(key, [])
        rows.append((estimate['input_bytes'], prompt))
        del rows[:-16]
        return {'reported_prompt_tokens': prompt, 'input_estimation_error': error}


class BudgetExceeded(ModelError):
    """Admission rejected before a model request; no tool operation was replayed."""


class BudgetClient:
    def __init__(self, client, budget, max_tokens, allocation=None, estimator_mode="calibrated"):
        self.client, self.budget, self.max_tokens = client, budget, max_tokens
        self.reason = None
        self.scope = None
        self.allocation = allocation
        self.estimator = InputEstimator(estimator_mode)

    def complete(self, messages, tools, on_text=None):
        self.reason = self.scope = None
        budgets = [('global', self.budget)]
        if self.allocation is not None:
            budgets.insert(0, ('run', self.allocation))
        estimate = self.estimator.estimate(messages, tools)
        reserve = estimate['estimated_input_tokens'] + self.max_tokens + 512
        details = {k: v for k, v in estimate.items() if k != 'schema_key'}
        for scope, b in budgets:
            b['last_estimated_request_tokens'] = reserve
            b['last_estimator'] = estimate['estimator']
            if time.monotonic() >= b.get('request_deadline', b['deadline']):
                self.reason = 'time_budget'
            elif b.get('max_calls') is not None and b['calls'] >= b['max_calls']:
                self.reason = 'call_budget'
            elif b.get('max_tokens') is not None and b['unknown_usage']:
                self.reason = 'usage_unavailable'
            elif b.get('max_tokens') is not None and b['tokens'] + reserve > b['max_tokens']:
                self.reason = 'token_budget'
            if self.reason:
                self.scope = scope
                break
        if self.reason:
            raise BudgetExceeded('运行预算停止：' + self.reason)
        for _, b in budgets:
            b['estimated_request_tokens'] = b.get('estimated_request_tokens', 0) + reserve
            b['calls'] += 1  # Failed HTTP requests also consume the attempt budget.
        try:
            result = self.client.complete(messages, tools, on_text)
        except BudgetExceeded:
            # An inner batch budget can reject without sending any HTTP request.
            for _, b in budgets:
                b['calls'] -= 1
                b['estimated_request_tokens'] -= reserve
            self.reason = getattr(self.client, 'reason', None) or 'external_budget'
            self.scope = getattr(self.client, 'scope', None)
            raise
        except BaseException:
            self.estimator.samples.pop(estimate['schema_key'], None)
            for _, b in budgets:
                b['unmetered_attempts'] = b.get('unmetered_attempts', 0) + 1
                record_request(b, reserve, None, 'error', details)
            raise
        used = result.usage.get('total_tokens')
        details.update(self.estimator.observe(estimate, result.usage))
        for _, b in budgets:
            record_request(b, reserve, used if type(used) is int and used >= 0 else None, 'returned', details)
            if details.get('input_estimation_error') is not None and details['input_estimation_error'] < 0:
                b['input_underestimates'] = b.get('input_underestimates', 0) + 1
            if type(used) is not int or used < 0:
                b['unknown_usage'] = True
            else:
                b['tokens'] += used
        return result

    def cancelled(self):
        return getattr(self.client, 'cancelled', lambda: False)()
