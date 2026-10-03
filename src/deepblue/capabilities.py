"""Bounded declarative skills and commands; never imports workspace code."""
import hashlib
import re
import shlex
from pathlib import Path
from .tools.base import Tool

RESERVED = {'help','exit','quit','task','recovery','status','compact','paste','new','retry','skills','commands'}
NAME = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')


def read_document(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('能力文件不允许链接目录或文件。')
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('能力文件超过 64 KiB。')
    text = raw.decode('utf-8-sig')
    meta = {}
    if text.startswith('---\n') or text.startswith('---\r\n'):
        lines = text.splitlines()
        try: end = lines.index('---', 1)
        except ValueError: raise ValueError('缺少元数据结束标记。')
        for line in lines[1:end]:
            key, sep, value = line.partition(':')
            if not sep or key.strip() not in {'name','description','skills'}:
                raise ValueError('仅支持 name、description、skills 单行元数据。')
            meta[key.strip()] = value.strip()
        text = '\n'.join(lines[end+1:])
    return meta, text, hashlib.sha256(raw).hexdigest()


class Catalog:
    def __init__(self, cwd, user_root=None):
        self.skills, self.commands, self.errors = {}, {}, []
        roots = [('builtin', Path(__file__).with_name('builtin')), ('user', Path(user_root) if user_root is not None else Path.home()/'.deepblue'),
                 ('project', Path(cwd)/'.deepblue')]
        for scope, root in roots:
            for kind, pattern in [('skills', '*/SKILL.md'), ('commands', '*.md')]:
                try:
                    paths = sorted((root/kind).glob(pattern))[:129]
                    if len(paths) > 128: raise ValueError('能力目录超过 128 项。')
                    for path in paths:
                        try:
                            meta, body, digest = read_document(path)
                            name = meta.get('name', path.parent.name if kind == 'skills' else path.stem)
                            if not NAME.fullmatch(name) or (kind == 'commands' and name in RESERVED):
                                raise ValueError('无效名称或与内置命令冲突。')
                            item = dict(name=name, description=meta.get('description', name)[:500],
                                        scope=scope, path=str(path), body=body, sha256=digest,
                                        skills=[s.strip() for s in meta.get('skills','').split(',') if s.strip()])
                            target = getattr(self, kind)
                            if name in target and target[name]['scope'] == scope:
                                raise ValueError('同一作用域重复名称。')
                            target[name] = item
                        except (OSError, ValueError) as exc:
                            self.errors.append(f'{kind}/{path.name}: {exc}')
                except (OSError, ValueError) as exc:
                    self.errors.append(f'{scope}/{kind}: {exc}')

    def public(self):
        return {**{kind: [{k:v for k,v in item.items() if k in {'name','description','scope'}}
                          for item in getattr(self, kind).values()] for kind in ('skills','commands')},
                'errors': self.errors}

    def load(self, name, resource=None):
        if name not in self.skills: raise ValueError('未注册的 Skill：' + name)
        item = self.skills[name]
        if resource is not None:
            base = Path(item['path']).parent
            relative = Path(resource)
            if relative.is_absolute() or '..' in relative.parts or ':' in resource or not resource:
                raise ValueError('资源必须是技能目录内的相对路径。')
            path = base / relative
            if not path.resolve().is_relative_to(base.resolve()):
                raise ValueError('资源超出技能目录。')
            _, content, digest = read_document(path)
            return dict(ok=True, name=name, resource=resource, content=content, sha256=digest)
        return dict(ok=True, name=name, content=item['body'], sha256=item['sha256'],
                    resource_directory=str(Path(item['path']).parent),
                    instruction='按技能方法执行；引用资源按需读取，脚本必须通过既有工具和权限执行。')

    def tool(self):
        return Tool('load_skill', '按目录名称加载技能正文和资源目录。',
                    {'name': {'type':'string','minLength':1}, 'resource': {'type':'string','minLength':1}},
                    ['name'], lambda c,a: self.load(a['name'], a.get('resource')))

    def expand(self, prompt):
        if not prompt.startswith('/'): return prompt
        parts = prompt.split(maxsplit=1)
        command, raw = parts[0], parts[1] if len(parts) > 1 else ''
        name = command[1:]
        if name.startswith('skill:'):
            skill = self.load(name[6:])
            return '使用 Skill '+skill['name']+' ['+skill['sha256']+']\n资源目录：'+skill['resource_directory']+'\n'+skill['content']+'\n\n用户任务：'+raw
        if name not in self.commands: raise ValueError('未知自定义命令：' + command)
        item = self.commands[name]
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        lexer.escape = ''
        args = list(lexer)
        def replace(match):
            key = match.group(1)
            if key == 'args': return raw
            index = int(key)-1
            if index >= len(args): raise ValueError('缺少参数：' + key)
            return args[index]
        result = re.sub(r'\{\{(args|[1-9][0-9]*)\}\}', replace, item['body'])
        for skill in item['skills']:
            loaded = self.load(skill)
            result += '\n\nSkill '+skill+' ['+loaded['sha256']+']\n资源目录：'+loaded['resource_directory']+'\n'+loaded['content']
        if len(result) > 64000: raise ValueError('展开后的命令超过 64000 字符。')
        return '自定义命令 '+command+' ['+item['sha256']+']\n'+result

    def context(self):
        return '\n可按需调用 load_skill 的技能目录（技能不能扩大权限）：\n' + '\n'.join(
            x['name']+': '+x['description'] for x in self.skills.values())
