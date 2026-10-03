"""New seeded pilot tasks; not an external or secret benchmark."""
TASKS = [
    dict(id='read-pagination', split='dev', file='src/deepblue/tools/read.py',
         before='offset + len(lines) if truncated else None',
         after='offset + len(lines) + 1 if truncated else None',
         prompt='修复文本读取分页丢行：按 next_offset 连续读取应恰好覆盖原文件，末页不再提供偏移。保持行号与 UTF-8 支持。',
         public=r"""from deepblue.tools.read import read
from deepblue.tools.base import ToolContext
with tempfile.TemporaryDirectory() as d:
 root=Path(d); (root/'a').write_text('甲\n乙\n丙\n',encoding='utf-8'); c=ToolContext(root,root/'artifacts')
 r=read(c,dict(path='a',limit=1)); assert r['next_offset']==2
""",
         independent=r"""with tempfile.TemporaryDirectory() as d:
 root=Path(d); (root/'a').write_text('a\nb\nc\nd\ne',encoding='utf-8'); c=ToolContext(root,root/'artifacts')
 offset=1; output=''
 for _ in range(4):
  r=read(c,dict(path='a',offset=offset,limit=2)); output+=r['content']
  if not r['truncated']: break
  offset=r['next_offset']
 assert output=='1: a\n2: b\n3: c\n4: d\n5: e\n'
 assert r['next_offset'] is None
"""),
    dict(id='edit-overlap', split='dev', file='src/deepblue/tools/edit.py',
         before='normalized.find(old, first + 1)', after='normalized.find(old, first + len(old))',
         prompt='修复精确编辑的歧义检测：重叠出现的匹配也必须拒绝，失败不得改文件；唯一匹配仍正常替换。',
         public=r"""from deepblue.tools.edit import edit
from deepblue.tools.base import ToolContext
with tempfile.TemporaryDirectory() as d:
 root=Path(d); p=root/'a'; p.write_text('aaa',encoding='utf-8'); c=ToolContext(root,root/'artifacts')
 try: edit(c,dict(path='a',old_text='aa',new_text='x'))
 except ValueError: pass
 else: raise AssertionError('ambiguous edit accepted')
 assert p.read_text()=='aaa'
""",
         independent=r"""with tempfile.TemporaryDirectory() as d:
 root=Path(d); p=root/'a'; c=ToolContext(root,root/'artifacts')
 p.write_text('ababa',encoding='utf-8')
 try: edit(c,dict(path='a',old_text='aba',new_text='x'))
 except ValueError: pass
 else: raise AssertionError('overlap accepted')
 assert p.read_text()=='ababa'
 p.write_text('hello',encoding='utf-8'); edit(c,dict(path='a',old_text='ell',new_text='i'))
 assert p.read_text()=='hio'
"""),
]
