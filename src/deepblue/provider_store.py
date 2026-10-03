"""Project-scoped connection profiles. Windows secrets use current-user DPAPI."""
import base64
import ctypes
import json
import os
from .web_sessions import atomic_json


def secret(value, decrypt=False):
    if os.name != 'nt': return value
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    raw = base64.b64decode(value) if decrypt else value.encode()
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('无法访问当前用户的 API Key 存储。')
    try:
        data = ctypes.string_at(result.data, result.size)
        return data.decode() if decrypt else base64.b64encode(data).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


class ProviderStore:
    def __init__(self, path):
        self.path = path

    def read(self):
        if not self.path.exists(): return None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data['version'] != 1 or data.get('encryption') != ('dpapi' if os.name == 'nt' else 'file-permissions'):
                raise ValueError()
            if not isinstance(data['profiles'],dict) or not 1 <= len(data['profiles']) <= 32 or data['active'] not in data['profiles']:
                raise ValueError()
            for profile in data['profiles'].values():
                value = profile.pop('credential', '')
                profile['api_key'] = secret(value, True) if value else ''
            return data
        except (ValueError, KeyError, TypeError):
            raise ValueError('API 配置无法读取；请检查配置文件或当前操作系统用户。') from None

    def write(self, active, profiles):
        data = json.loads(json.dumps(profiles))
        for profile in data.values():
            key = profile.pop('api_key', '')
            profile['credential'] = secret(key) if key else ''
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt': os.chmod(self.path.parent, 0o700)
        atomic_json(self.path, dict(version=1, encryption='dpapi' if os.name == 'nt' else 'file-permissions', active=active, profiles=data))
        if os.name != 'nt': os.chmod(self.path, 0o600)
