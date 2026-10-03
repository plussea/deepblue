import tempfile
import unittest
from pathlib import Path
from deepblue.capabilities import Catalog
from deepblue.tools import create_tools, ToolContext

class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.user = self.root/'user'

    def put(self, root, path, text):
        target=root/path; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(text,encoding='utf-8')
        return target

    def catalog(self): return Catalog(self.project, self.user)

    def test_override_expand_and_frozen_skill(self):
        self.put(self.user,'commands/review.md','user')
        self.put(self.project/'.deepblue','commands/review.md','---\nskills: audit\n---\nReview {{1}} / {{args}}')
        skill=self.put(self.project/'.deepblue','skills/audit/SKILL.md','---\ndescription: inspect changes\n---\nRun checks')
        c=self.catalog(); skill.write_text('changed',encoding='utf-8')
        expanded=c.expand('/review "src dir"')
        self.assertIn('Review src dir / "src dir"',expanded)
        self.assertIn('Run checks',expanded)
        self.assertNotIn('changed',expanded)
        self.assertEqual(next(x for x in c.public()['commands'] if x['name']=='review')['scope'],'project')
        self.assertEqual(len(c.load('audit')['sha256']),64)
        self.assertNotIn('Run checks',c.context())

    def test_invalid_files_missing_arguments_and_unknown_skill(self):
        self.put(self.project/'.deepblue','commands/help.md','reserved')
        self.put(self.project/'.deepblue','commands/big.md','x'*65537)
        self.put(self.project/'.deepblue','commands/go.md','{{2}}')
        self.put(self.project/'.deepblue','commands/skill.md','---\nskills: missing\n---\nhi')
        c=self.catalog(); self.assertEqual(len(c.errors),2)
        for prompt in ['/go one','/skill','/missing']:
            with self.assertRaises(ValueError): c.expand(prompt)
        self.assertEqual(c.expand('hello'),'hello')

    def test_replacement_is_not_recursive_or_shell_execution(self):
        self.put(self.project/'.deepblue','commands/go.md','{{1}}')
        self.assertIn('{{2}}',self.catalog().expand('/go "{{2}}"'))
        self.assertIn('$(whoami)',self.catalog().expand('/go "$(whoami)"'))

    def test_read_only_skill_load_does_not_enable_shell(self):
        self.put(self.project/'.deepblue','skills/a/SKILL.md','Use read')
        context=ToolContext(self.project,self.root/'artifacts',permission_mode='read-only')
        tools=create_tools(context);tools.register(self.catalog().tool())
        self.assertTrue(tools.execute('load_skill','{"name":"a"}')['ok'])
        self.assertFalse(tools.execute('shell','{"command":"echo hi"}')['ok'])
        self.assertFalse(tools.execute('load_skill','{"name":"../a"}')['ok'])

    def test_resources_are_bounded_and_cannot_escape(self):
        self.put(self.project/'.deepblue','skills/a/SKILL.md','Read reference.md')
        self.put(self.project/'.deepblue','skills/a/reference.md','example')
        c=self.catalog()
        self.assertEqual(c.load('a','reference.md')['content'],'example')
        for resource in ['../outside', str(self.root.resolve()), 'C:secret']:
            with self.assertRaises(ValueError): c.load('a',resource)

    def test_agent_expands_and_records_command_with_skill(self):
        from deepblue.agent import Agent
        from deepblue.config import Config
        from deepblue.session import Session
        from test_agent import FakeClient, response
        self.put(self.project/'.deepblue','commands/go.md','---\nskills: a\n---\nDo {{1}}')
        self.put(self.project/'.deepblue','skills/a/SKILL.md','Verify changes')
        config=Config(self.project,'fixture',home=self.root/'home',auto_compact=False)
        session=Session.create(config.home,config.cwd,config.model,'system')
        self.addCleanup(session.close)
        client=FakeClient([response(reason='stop',content='done')])
        agent=Agent(config,client,create_tools(ToolContext(config.cwd,session.artifacts)),session)
        agent.run('/go "C:\\work"')
        message=next(m for m in session.messages if m['role']=='user')['content']
        self.assertIn('Verify changes',message)
        self.assertIn('自定义命令 /go [',message)
        self.assertIn('C:\\work',message)
        self.assertIn('load_skill',client.requests[0][0]['content'])

    def test_bundled_commands_and_creator_are_discoverable(self):
        c=self.catalog()
        self.assertEqual(c.errors,[])
        for name in ['review','explain','fix','test','plan','create-skill']:
            self.assertEqual(c.commands[name]['scope'],'builtin')
            self.assertNotIn('{{args}}',c.expand('/'+name+' example'))
        self.assertIn('Skill skill-creator [',c.expand('/create-skill example'))
        self.put(self.user,'commands/review.md','user review')
        self.assertEqual(self.catalog().commands['review']['scope'],'user')

    def test_explicit_skill_entry_does_not_collide_with_commands(self):
        c=self.catalog()
        prompt=c.expand('/skill:skill-creator 创建测试技能')
        self.assertIn('使用 Skill skill-creator [',prompt)
        self.assertIn('用户任务：创建测试技能',prompt)
        with self.assertRaises(ValueError): c.expand('/skill:missing hi')
