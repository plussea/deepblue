"""Read-only backend diagnostics. Availability is never sandbox verification."""
from __future__ import annotations

import json
import os
import shutil
import subprocess


def probe(argv):
    try:
        result = subprocess.run(argv, capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout'}
    except OSError:
        return {'status': 'unavailable'}
    if result.returncode:
        return {'status': 'error', 'exit_code': result.returncode}
    data = result.stdout
    text = data.decode('utf-16-le' if b'\0' in data else 'utf-8', errors='replace')
    return {'status': 'available', 'text': text[:8192]}


def diagnostics():
    report = dict(schema_version=1, isolation_verified=False,
                  execution_backend='host', restricted_shell='denied', backends={})
    docker = shutil.which('docker')
    if docker:
        result = probe([docker, 'version', '--format', '{{json .Server}}'])
        if result['status'] == 'available':
            try:
                server = json.loads(result.pop('text'))
                if not isinstance(server, dict) or not server.get('Version'):
                    raise ValueError('missing server')
                result['server_version'] = server['Version']
                result['os'] = server.get('Os')
            except (ValueError, TypeError):
                result = {'status': 'invalid_response'}
        report['backends']['docker'] = result
    else:
        report['backends']['docker'] = {'status': 'not_found'}
    bwrap = shutil.which('bwrap')
    if bwrap:
        result = probe([bwrap, '--version'])
        result.pop('text', None)
        report['backends']['bubblewrap'] = result
    else:
        report['backends']['bubblewrap'] = {'status': 'not_found'}
    if os.name == 'nt':
        wsl = shutil.which('wsl')
        result = probe([wsl, '--list', '--quiet']) if wsl else {'status': 'not_found'}
        if 'text' in result:
            result['distributions'] = [line.strip() for line in result.pop('text').splitlines() if line.strip()]
        report['backends']['wsl'] = result
    report['notice'] = '仅诊断工具/服务可用性；未创建容器、未拉取镜像、未执行项目命令，不证明隔离有效。'
    return report
