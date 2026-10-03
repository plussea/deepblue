"""Fork only complete transcript prefixes; no execution evidence is inherited."""
import copy
from .session import Session


def fork_session(source, home, cwd, count=None):
    messages = source.messages
    count = len(messages) if count is None else count
    if type(count) is not int or not 1 <= count <= len(messages):
        raise ValueError('无效分叉消息位置。')
    selected = copy.deepcopy(messages[:count])
    pending = set()
    for message in selected:
        if pending and message['role'] != 'tool':
            raise ValueError('分叉位置存在未配对工具调用。')
        if message['role'] == 'assistant':
            ids = [call['id'] for call in message.get('tool_calls', [])]
            if len(ids) != len(set(ids)):
                raise ValueError('重复工具调用 ID。')
            pending.update(ids)
        elif message['role'] == 'tool':
            if message.get('tool_call_id') not in pending:
                raise ValueError('孤立工具结果，不能分叉。')
            pending.remove(message['tool_call_id'])
    if pending:
        raise ValueError('请在完整工具批次结束后分叉。')
    child = Session.create(home, cwd, source.header['model'], selected[0]['content'],
                           parent={'session_id': source.header['id'], 'message_count': count})
    try:
        for message in selected[1:]:
            child.add(message)
        return child.header['id']
    finally:
        child.close()
