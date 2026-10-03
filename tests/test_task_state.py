import json
import tempfile
import unittest
from pathlib import Path

from deepblue.agent import Agent
from deepblue.config import Config
from deepblue.session import Session
from deepblue.task_state import begin, view
from deepblue.tools import ToolContext, create_tools
from deepblue.web_sessions import SessionIndex
from deepblue.verification import VerificationConfig
from test_agent import FakeClient, response, tool_call
from test_tools import python_command


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base/'workspace'
        self.root.mkdir()
        self.config = Config(self.root,'fake',home=self.base/'home', max_steps=3)
        self.session = Session.create(self.config.home,self.root,'fake','system')
        self.addCleanup(self.session.close)

    def agent(self, responses, verification=None):
        self.client = FakeClient(responses)
        return Agent(self.config,self.client,create_tools(ToolContext(self.root,self.session.artifacts)),self.session,verification=verification)

    def reload(self):
        path=self.session.path
        self.session.close()
        self.session=Session.load(path,self.root)
        self.addCleanup(self.session.close)

    def test_notes_cannot_forge_runtime_and_retry_keeps_goal(self):
        self.config.max_steps=1
        agent=self.agent([response(tool_call('task_update',{'progress':'已定位','blockers':'待修复','next_step':'修改 calc.py'})),response(reason='stop')])
        first=agent.run('修复 calc.py')
        self.assertEqual(self.session.task_state['notes']['next_step'],'修改 calc.py')
        self.reload()
        self.assertEqual(self.session.task_state['goal'],'修复 calc.py')
        second=self.agent([response(reason='stop')]).run()
        self.assertEqual(first.task_id,second.task_id)
        self.assertEqual(self.session.task_state['goal'],'修复 calc.py')
        self.assertIn('已定位',self.client.requests[0][0]['content'])
        self.assertEqual(self.session.task_state['runtime']['execution_status'],'finished')

    def test_tool_metrics_persist_in_run_and_task_state(self):
        (self.root / 'code.py').write_text('def target(): pass')
        self.agent([response(tool_call('symbols', {'query': 'target'})),
                    response(reason='stop')]).run('locate target')
        self.assertEqual(self.session.last_run['tool_metrics']['tool_calls'], 1)
        self.assertEqual(self.session.task_state['runtime']['tool_metrics'],
                         self.session.last_run['tool_metrics'])
        self.reload()
        self.assertEqual(self.session.task_state['runtime']['tool_metrics']['tool_calls'], 1)

    def test_new_task_does_not_inherit_model_notes(self):
        self.agent([response(tool_call('task_update',{'blockers':'old blocker'})),response(reason='stop')]).run('old goal')
        old=self.session.task_state['task_id']
        self.agent([response(reason='stop')]).run('new goal')
        self.assertNotEqual(old,self.session.task_state['task_id'])
        self.assertEqual(self.session.task_state['notes']['blockers'],'')

    def test_illegal_notes_are_rejected_without_changing_facts(self):
        self.config.max_steps=1
        for args in ({'execution_status':'finished'},{'progress':'x'*2001},{}):
            self.agent([response(tool_call('task_update',args))]).run('goal')
            self.assertFalse(json.loads(self.session.messages[-1]['content'])['ok'])
            self.assertEqual(self.session.task_state['goal'],'goal')
            self.assertEqual(self.session.task_state['runtime']['execution_status'],'step_limit')
            self.assertEqual(self.session.task_state['notes']['progress'],'')

    def test_compaction_does_not_drop_task_or_modify_original_system(self):
        self.agent([response(tool_call('task_update',{'next_step':'keep exact marker'})),response(reason='stop')]).run('original goal')
        self.session.add({'role':'user','content':'later'})
        cutoff=len(self.session.messages)-1
        self.session.save_compaction('compressed history',cutoff)
        self.reload()
        context=self.session.context_messages()
        self.assertIn('original goal',context[0]['content'])
        self.assertIn('keep exact marker',context[0]['content'])
        self.assertEqual(self.session.messages[0]['content'],'system')
        self.assertIn('compressed history',context[1]['content'])

    def test_old_sessions_and_interrupted_task_do_not_replay(self):
        self.reload()
        self.assertIsNone(view(self.session))
        begin(self.session,'task','run','original',None)
        self.session.add({'role':'user','content':'original'})
        self.reload()
        self.assertEqual(view(self.session)['runtime']['execution_status'],'interrupted')
        self.assertEqual(self.session.task_state['runtime']['execution_status'],'running')
        self.assertEqual(self.session.operations,{})

    def test_incremental_index_restores_task_and_ignores_partial_record(self):
        index=SessionIndex()
        self.agent([response(reason='stop')]).run('indexed goal')
        state=index.read(self.session.path,self.root).task_state
        self.assertEqual(state['goal'],'indexed goal')
        self.agent([response(reason='stop')]).run('second goal')
        self.assertEqual(index.read(self.session.path,self.root).task_state['goal'],'second goal')
        with self.session.path.open('ab') as stream: stream.write(b'{"type":"task_state"')
        before=self.session.path.read_bytes()
        self.assertEqual(index.read(self.session.path,self.root).task_state['goal'],'second goal')
        self.assertEqual(before,self.session.path.read_bytes())

    def test_runtime_evidence_and_changed_files_survive_restart(self):
        verification=VerificationConfig(python_command("from pathlib import Path; assert Path('answer').read_text()=='good'"),self.root,10,0)
        result=self.agent([response(tool_call('write',{'path':'answer','content':'good'})),response(reason='stop')],verification).run('fix answer')
        self.assertTrue(result.successful)
        self.reload()
        task=view(self.session)
        self.assertEqual(task['runtime']['verification_status'],'passed')
        self.assertEqual(task['runtime']['touched_files'],['answer'])
        self.assertEqual(task['acceptance']['command'],verification.command)
        (self.root/'answer').write_text('bad')
        self.assertEqual(view(self.session)['runtime']['verification_status'],'stale')

    def test_invalid_state_is_not_appended(self):
        before=self.session.path.read_bytes()
        with self.assertRaises(ValueError): self.session.record_task_state({'schema_version':9})
        self.assertEqual(before,self.session.path.read_bytes())
