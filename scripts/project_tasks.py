"""Frozen, seeded regressions in real DeepBlue modules, not historical issue fixes.

The evaluation author can see all assertions; holdout means excluded from agent
prompt/feedback and model-guided tuning, not a secret or external benchmark.
"""
REVISION = 'e05688f4badc5e426c82148f14413b389bb442eb'

COMMON = "import sys, os, tempfile, json\nfrom pathlib import Path\nsys.path.insert(0, str(Path('src').resolve()))\n"

TASKS = [
    dict(id='completion-url', split='dev', file='src/deepblue/config.py',
         before='if self.base_url.endswith("/chat/completions"):',
         after='if False:  # regression: full endpoint is treated as a root',
         prompt='修复 DeepSeek 地址规范化：既接受 API 根地址，也接受完整 /chat/completions 地址，不能重复拼接；保留现有安全校验。',
         public="from deepblue.config import Config\nc=Config(Path.cwd(), 'fixture', base_url='https://api.deepseek.com/chat/completions')\nassert c.completion_url == c.base_url\n",
         independent="from deepblue.config import Config\nfor url, expected in [('https://example.com/v1/', 'https://example.com/v1/chat/completions'), ('https://example.com/v1/chat/completions/', 'https://example.com/v1/chat/completions')]:\n assert Config(Path.cwd(), 'fixture', base_url=url).completion_url == expected\nfor url in ['https://user:pass@example.com', 'http://example.com', 'https://example.com?key=x']:\n try: Config(Path.cwd(), 'fixture', base_url=url)\n except ValueError: pass\n else: raise AssertionError('unsafe URL accepted')\n"),
    dict(id='recursive-glob', split='dev', file='src/deepblue/tools/search.py',
         before=' or (pattern.startswith("**/") and matches(path, pattern[3:]))', after='',
         prompt='修复 find/grep 共用的 glob 匹配：**/*.py 应同时匹配根目录和嵌套文件；保持大小写敏感、路径模式与文件名模式原有语义。',
         public="from deepblue.tools.search import matches\nassert matches('main.py', '**/*.py')\nassert matches('src/main.py', '**/*.py')\n",
         independent="from deepblue.tools.search import matches\nfor name in ['a.py', 'a/b.py', 'a/b/c.py']:\n assert matches(name, '**/*.py')\nassert not matches('a.PY', '**/*.py')\nassert not matches('a.txt', '**/*.py')\nassert matches('src/a.py', 'src/*.py')\nassert not matches('other/a.py', 'src/*.py')\nassert matches('any/a.py', '*.py')\n"),
    dict(id='jsonl-tail', split='dev', file='src/deepblue/web_sessions.py',
         before="                    if not raw.endswith(b'\\n'):\n                        break\n", after='',
         prompt='修复 Web 会话增量索引：JSONL 写入中的半行尾部必须等待下一次读取，不能报损坏、修改文件或重复消息。完整行的格式错误仍应报错。',
         public="from deepblue.web_sessions import SessionIndex\nwith tempfile.TemporaryDirectory() as d:\n root=Path(d).resolve(); p=root/'s.jsonl'\n header=json.dumps(dict(type='session', version=1, cwd=str(root), id='test'))+'\\n'\n p.write_text(header+'{\"type\":', encoding='utf-8')\n original=p.read_bytes(); s=SessionIndex().read(p, root)\n assert s.messages == [] and p.read_bytes() == original\n",
         independent="from deepblue.web_sessions import SessionIndex\nwith tempfile.TemporaryDirectory() as d:\n root=Path(d).resolve(); p=root/'s.jsonl'; idx=SessionIndex()\n h=json.dumps(dict(type='session', version=1, cwd=str(root), id='test'))+'\\n'\n m=json.dumps(dict(type='message', message=dict(role='user', content='中文')), ensure_ascii=False)+'\\n'\n raw=m.encode(); p.write_bytes(h.encode()+raw[:17]); idx.read(p,root)\n with p.open('ab') as f: f.write(raw[17:])\n assert idx.read(p,root).messages == [dict(role='user',content='中文')]\n assert len(idx.read(p,root).messages)==1\n with p.open('ab') as f: f.write(b'broken\\n')\n try: idx.read(p,root)\n except ValueError: pass\n else: raise AssertionError('corrupt complete line accepted')\n"),
    dict(id='compaction-range', split='holdout', file='src/deepblue/config.py',
         before='if not 0.2 <= self.compact_threshold <= 0.95:', after='if self.compact_threshold < 0.2:',
         prompt='修复 Config 的自动压缩阈值校验：只接受 0.2 到 0.95 的有限数值，边界可用，其他阈值抛 ValueError；不改变默认值及其他配置行为。',
         public="from deepblue.config import Config\ntry: Config(Path.cwd(),'fixture',compact_threshold=1.1)\nexcept ValueError: pass\nelse: raise AssertionError('high threshold accepted')\n",
         independent="from deepblue.config import Config\nfor v in [float('nan'),float('inf'),float('-inf'),.199,.951]:\n try: Config(Path.cwd(),'fixture',compact_threshold=v)\n except ValueError: pass\n else: raise AssertionError('invalid threshold accepted')\nfor v in [.2,.8,.95]:\n assert Config(Path.cwd(),'fixture',compact_threshold=v).compact_threshold==v\n"),
    dict(id='web-storage', split='holdout', file='src/deepblue/web.py',
         before="explicit or os.getenv('DEEPBLUE_HOME') or Path(cwd) / '.deepblue'",
         after="explicit or os.getenv('DEEPBLUE_HOME') or Path.home() / '.deepblue'",
         prompt='修复 Web 默认存储路径：未显式配置时使用项目目录内 .deepblue；优先级为 explicit > DEEPBLUE_HOME > 项目默认。返回展开并解析后的路径，不能创建目录或隐式迁移会话。',
         public="from deepblue.web import storage_home\nos.environ.pop('DEEPBLUE_HOME',None)\nwith tempfile.TemporaryDirectory() as d:\n root=Path(d).resolve(); assert storage_home(root)==root/'.deepblue'\n",
         independent="from deepblue.web import storage_home\nwith tempfile.TemporaryDirectory() as d:\n root=Path(d).resolve(); os.environ['DEEPBLUE_HOME']=str(root/'environment')\n assert storage_home(root)==root/'environment'\n assert storage_home(root,root/'explicit')==root/'explicit'\n os.environ.pop('DEEPBLUE_HOME')\n assert storage_home(root)==root/'.deepblue'\n assert list(root.iterdir())==[]\n"),
]
