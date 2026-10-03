"""Diagnostic per-run counts rebuilt from completed operation IDs, never replayed."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def operation_metrics(session, run_id):
    metrics = dict(tool_calls=0, read_calls=0, repeated_reads=0, failed_calls=0,
                   repeated_failures=0, calls_before_first_file_change=None, tool_seconds=0.0)
    reads, failures = set(), set()
    for op in session.operations.values():
        if op.get('run_id') != run_id or op.get('phase') != 'finished':
            continue
        metrics['tool_calls'] += 1
        metrics['tool_seconds'] += op.get('elapsed_seconds', 0)
        result = op.get('result', {})
        if op['name'] == 'read':
            metrics['read_calls'] += 1
            key = op.get('read_signature')
            if key:
                metrics['repeated_reads'] += int(key in reads)
                reads.add(key)
        if not result.get('ok'):
            metrics['failed_calls'] += 1
            key = op.get('failure_signature')
            if key:
                metrics['repeated_failures'] += int(key in failures)
                failures.add(key)
        if op['name'] in {'write', 'edit'} and result.get('ok') and op.get('before') != op.get('after'):
            if metrics['calls_before_first_file_change'] is None:
                metrics['calls_before_first_file_change'] = metrics['tool_calls'] - 1
        # Commands can change unobserved files even on failure. Reset the read cache;
        # failure recurrence is deliberately an outcome metric, not wasted work.
        if op['name'] == 'shell':
            reads.clear()
    metrics['tool_seconds'] = round(metrics['tool_seconds'], 3)
    return metrics
