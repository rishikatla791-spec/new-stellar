import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as A
import agent_workflow as W
import deployment_checks as D


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.workflow=W.BuildRun(self.db,'q',1,1,'Build and deploy an application')
    def test_build_intent_not_advice(self):
        self.assertTrue(W.is_build_task('Build and deploy a complex app'))
        self.assertFalse(W.is_build_task('How do I build and deploy an app?'))
    def test_checkpoints_beyond_eight(self):
        for _ in range(17):self.workflow.advance()
        row=W.latest(self.db,1,1);self.assertEqual(row['steps'],17);self.assertEqual(row['checkpoints'],2)
        self.assertFalse(self.workflow.exhausted());self.assertIsNone(W.latest(self.db,1,2))
    def test_evidence_requires_all_phases(self):
        self.workflow.observe('repo_control',{'action':'deploy'},'**Process ID**: `abcdef123456`',False)
        self.assertEqual(len(self.workflow.missing()),3)
        self.workflow.observe('repo_control',{'action':'execute','app_id':'abcdef123456','command':'python3 -m unittest'},'Ran 2 tests\nOK',False)
        self.workflow.observe('repo_control',{'action':'serve','app_id':'abcdef123456'},'READY',False)
        self.workflow.observe('repo_control',{'action':'verify','app_id':'abcdef123456'},'VERIFIED DEPLOYMENT: {}',False)
        self.assertFalse(self.workflow.missing())
        self.workflow.observe('repo_control',{'action':'execute','app_id':'abcdef123456','command':'echo edit'},'OK',False)
        self.assertEqual(len(self.workflow.missing()),2)
    def test_failed_and_background_tests_are_not_passes(self):
        for args,failed in [({'action':'execute','app_id':'abcdef123456','command':'pytest'},True),({'action':'execute','app_id':'abcdef123456','command':'pytest','background':True},False)]:
            self.workflow.observe('repo_control',args,'Started',failed)
        self.assertFalse(self.workflow.evidence['tests'])
    def test_writing_test_file_is_not_running_tests(self):
        self.workflow.observe('repo_control',{'action':'execute','app_id':'abcdef123456','command':"echo pytest > tests.py"},'OK',False)
        self.assertFalse(self.workflow.evidence['tests'])
    def test_zero_discovered_tests_not_a_pass(self):
        self.workflow.observe('repo_control',{'action':'execute','app_id':'abcdef123456','command':'python3 -m unittest'},'Ran 0 tests\nOK',False)
        self.assertFalse(self.workflow.evidence['tests'])
    def test_failed_mutation_invalidates_old_verification(self):
        self.workflow.evidence['projects']['abcdef123456']={'served':True,'verified':True,'tests':True}
        self.workflow.observe('repo_control',{'action':'execute','app_id':'abcdef123456','command':'write files'},'Partial write failed',True)
        self.assertFalse(self.workflow.evidence['projects']['abcdef123456']['verified'])
        self.assertFalse(self.workflow.evidence['projects']['abcdef123456']['tests'])
    def test_blocked_state_survives_reopen_and_is_resumable(self):
        self.workflow.advance();self.workflow.update('blocked','Provider unavailable')
        self.assertTrue(W.latest(self.db,1,1)['can_resume'])
    def test_bounded_runtime_and_steps(self):
        self.workflow.steps=W.BUILD_STEPS;self.assertTrue(self.workflow.exhausted())
        self.workflow.steps=1;self.workflow.started-=W.BUILD_SECONDS+1;self.assertTrue(self.workflow.exhausted())
    def test_context_keeps_request_and_real_verification_gaps(self):
        self.workflow.request='Build bookings with database persistence'
        note=self.workflow.context_note()
        self.assertIn('database persistence',note)
        self.assertIn('create or identify',note)
        self.assertIn('does not prove',note)
        self.assertNotIn('command',self.workflow.evidence)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.manager=A.KeyManager()
    def test_hidden_swift_models_remain_available_for_tool_recovery(self):
        swift=A.routing.TIER_MODELS['swift'][0]
        with patch.object(A,'selectable_models',return_value=[A.DEFAULT_MODEL]),patch.object(A,'available_models',return_value={A.DEFAULT_MODEL,swift}):
            chain=A._model_chain(A.DEFAULT_MODEL)
            self.assertIn(swift,chain)
            self.assertEqual(chain[0],A.DEFAULT_MODEL)
            self.assertEqual(len(chain),len(set(chain)))
            self.assertNotIn(A.routing.TIER_MODELS['swift'][1],chain)
    def test_minute_counter_waits_until_sliding_window_frees(self):
        with patch.object(A,'get_limits',return_value=(2,100)),patch.object(A.time,'time',return_value=1000):
            self.manager._counts_rpm[(self.manager._fingerprint('fixture'),'model')]=[970,980]
            self.assertIsNone(self.manager.first_available(['fixture'],'model'))
            self.assertEqual(self.manager.short_retry_after(['fixture'],['model']),32)
    def test_daily_and_invalid_and_missing_keys_never_short_retry(self):
        for reason in ('RPD','INVALID','MISSING'):
            manager=A.KeyManager()
            with patch.object(A.time,'time',return_value=1000):
                manager.block('fixture','model',10,reason)
                self.assertIsNone(manager.short_retry_after(['fixture'],['model']))
        with patch.object(A,'get_limits',return_value=(2,1)),patch.object(A.time,'time',return_value=1000):
            self.manager.record_request('fixture','model')
            self.assertIsNone(self.manager.short_retry_after(['fixture'],['model']))
    def test_long_cooldown_is_not_waited_and_shortest_pair_selected(self):
        with patch.object(A.time,'time',return_value=1000):
            self.manager.block('one','model',200,'RPM')
            self.manager.block('two','model',12,'RPM')
            self.assertEqual(self.manager.short_retry_after(['one','two'],['model']),14)
            self.assertIsNone(self.manager.short_retry_after(['one'],['model']))
    def test_redis_cooldown_uses_ttl_and_counter(self):
        manager=A.KeyManager();r=MagicMock();manager._client=r
        r.get.return_value=None;r.ttl.return_value=-2
        r.zrange.return_value=[('request',980)]
        with patch.object(A.time,'time',return_value=1000),patch.object(A,'get_limits',return_value=(1,100)),patch.object(manager,'_over_limit',return_value='RPM'):
            self.assertEqual(manager.short_retry_after(['fixture'],['model']),42)
            r.get.side_effect=lambda key:'INVALID' if key.endswith(':*') else None
            self.assertIsNone(manager.short_retry_after(['fixture'],['model']))
    def test_capacity_rechecks_after_wait_without_clearing_blocks(self):
        build=NS(started=A.time.monotonic(),exhausted=lambda:False,update=MagicMock())
        with patch.object(A.KEY_MANAGER,'short_retry_after',return_value=5),patch.object(A.KEY_MANAGER,'first_available',return_value=0),patch.object(A.KEY_MANAGER,'clear_model_block') as clear,patch.object(A,'_wait_unless',return_value=False) as wait:
            gen=A._build_capacity(build,['fixture'],['model'],lambda:False)
            self.assertIn('Waiting 5',next(gen)['text'])
            with self.assertRaises(StopIteration) as result:next(gen)
            self.assertEqual(result.exception.value,('model',0));wait.assert_called_once();clear.assert_not_called()
    def test_cancelled_or_daily_or_out_of_time_never_retries(self):
        for delay,cancelled,age in [(None,False,0),(5,True,0),(5,False,W.BUILD_SECONDS)]:
            build=NS(started=A.time.monotonic()-age,exhausted=lambda:False,update=MagicMock())
            with patch.object(A.KEY_MANAGER,'short_retry_after',return_value=delay),patch.object(A,'_wait_unless') as wait:
                self.assertEqual(list(A._build_capacity(build,['fixture'],['model'],lambda:cancelled)),[])
                wait.assert_not_called()
        build=NS(started=A.time.monotonic(),exhausted=lambda:False,update=MagicMock())
        with patch.object(A.KEY_MANAGER,'short_retry_after',return_value=5),patch.object(A.KEY_MANAGER,'first_available') as first,patch.object(A,'_wait_unless',return_value=True):
            gen=A._build_capacity(build,['fixture'],['model'],lambda:False);next(gen)
            with self.assertRaises(StopIteration) as result:next(gen)
            self.assertEqual(result.exception.value,(None,None));first.assert_not_called()


class PublicChecks(unittest.TestCase):
    def response(self,code=200,body=b'ok',kind='text/html',location=None):
        r=MagicMock();r.status_code=code;r.headers={'Content-Type':kind}
        if location:r.headers['Location']=location
        r.iter_content.return_value=[body];r.__enter__.return_value=r
        return r
    def test_owned_assets_checked_and_external_assets_skipped(self):
        session=MagicMock();session.get.side_effect=[self.response(body=b'<script src="/app.js"></script><script src="https://cdn.example.org/a.js"></script>'),self.response(kind='text/javascript')]
        result=D.verify('https://demo.example.org/',session=session)
        self.assertEqual(result['same_origin_assets'],['/app.js']);self.assertEqual(session.get.call_count,2)
    def test_off_origin_redirect_rejected_before_following(self):
        session=MagicMock();session.get.return_value=self.response(302,location='http://127.0.0.1/private')
        with self.assertRaises(ValueError):D.verify('https://demo.example.org/',session=session)
        self.assertEqual(session.get.call_count,1)
    def test_missing_asset_and_public_502_are_failures(self):
        for responses in [[self.response(502)],[self.response(body=b'<script src="/missing.js"></script>'),self.response(404)]]:
            session=MagicMock();session.get.side_effect=responses
            with self.assertRaises(ValueError):D.verify('https://demo.example.org/',session=session)
    def test_html_fallback_is_not_a_valid_script(self):
        session=MagicMock();session.get.side_effect=[self.response(body=b'<script src="/app.js"></script>'),self.response(body=b'<html>Fallback</html>')]
        with self.assertRaises(ValueError):D.verify('https://demo.example.org/',session=session)
    def test_large_response_and_bad_tunnel_rejected(self):
        session=MagicMock();session.get.return_value=self.response(body=b'x'*(D.MAX_RESPONSE+1))
        with self.assertRaises(ValueError):D.verify('https://demo.example.org/',session=session)
        with self.assertRaises(ValueError):D.verify('https://demo.example.org/','https://evil.example.org/',session)


class BuildApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.app=A.create_app({'TESTING':True,'BACKGROUND_THREADS':False,'SECRET_KEY':'workflow-test-only','CSRF_TOKENS':False,
            'DATABASE':str(root/'db'),'OUTPUTS_DIR':str(root/'out'),'DEPLOYMENTS_DIR':str(root/'deploy'),'SANDBOX_DIR':str(root/'sandbox')})
        with self.app.app_context():
            A.init_db();db=A.get_db()
            db.executemany('INSERT INTO users(id,username,password_hash,is_approved) VALUES(?,?,?,1)',[(1,'one@example.org',''),(2,'two@example.org','')])
            db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Build')");db.commit()
            run=W.BuildRun(db,'initial',1,1,'Build and deploy an app');run.update('blocked','Quota')
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:session['user_id']=1;session['epoch']=0
    def test_ownership_and_resume_does_not_replay_commands(self):
        self.assertEqual(self.client.get('/api/chats/1/build-run').status_code,200)
        with patch.object(A,'register_query',return_value='next') as register,patch.object(A,'rate_limited',return_value=False):
            response=self.client.post('/api/chats/1/build-run/resume',json={});self.assertEqual(response.status_code,202)
            queued=register.call_args.args[1];self.assertNotIn('command',queued);self.assertEqual(queued['_build_request'],'Build and deploy an app')
            self.assertEqual(self.client.post('/api/chats/1/build-run/resume',json={}).status_code,409)
        with self.client.session_transaction() as session:session['user_id']=2
        self.assertEqual(self.client.get('/api/chats/1/build-run').status_code,404)
        self.assertEqual(self.client.post('/api/chats/1/build-run/resume',json={}).status_code,404)
    def test_queue_failure_retains_checkpoint(self):
        with patch.object(A,'register_query',side_effect=RuntimeError('down')),patch.object(A,'rate_limited',return_value=False):
            self.assertEqual(self.client.post('/api/chats/1/build-run/resume',json={}).status_code,503)
        self.assertTrue(self.client.get('/api/chats/1/build-run').json['can_resume'])
    def test_provider_error_is_persisted(self):
        args={'chat_id':1,'user_id':1,'message':'Build and deploy app','_query_id':'provider-error'}
        with self.app.app_context(),patch.object(A,'_generate_turn',return_value=iter([{'type':'error','message':'Provider quota'}])),patch.object(A,'release_generation'):
            events=list(A.gemini_producer(MagicMock(),args))
            self.assertIn('Build paused',events[0]['text'])
            self.assertEqual(W.latest(A.get_db(),1,1)['status'],'blocked')
            self.assertIn('Provider quota',A.get_db().execute('SELECT message_content FROM messages ORDER BY id DESC LIMIT 1').fetchone()[0])
    def test_actual_producer_continues_past_eight_and_checks_completion(self):
        project='abcdef123456'
        def call(action,command=''):
            args={'action':action,'status':'Build test','app_id':project,'command':command}
            return NS(candidates=[NS(content=NS(parts=[NS(thought=False,text=None,function_call=NS(name='repo_control',args=args))]))],usage_metadata=None)
        chunks=[call('deploy')]+[call('execute','echo build-step') for _ in range(9)]
        chunks += [call('execute','python3 -m unittest'),call('serve'),call('verify')]
        chunks += [NS(candidates=[NS(content=NS(parts=[NS(thought=False,text='Verified build complete.',function_call=None)]))],usage_metadata=None)]
        chat=MagicMock();chat.send_message_stream.side_effect=lambda msg:iter([chunks.pop(0)])
        client=MagicMock();client.chats.create.return_value=chat
        def tool(name,args,cancelled):
            return ({'deploy':f'**Process ID**: `{project}`','execute':'Ran 2 tests\nOK' if args['command']=='python3 -m unittest' else 'Build output','serve':'Server is READY','verify':'VERIFIED DEPLOYMENT: {}'}[args['action']],False)
        patches={'register_generation':threading.Event(),'is_stopped':False,'release_generation':None,
                 '_drain_injections':[],'_end_turn_if_quiet':True,'_finalize_once':None,
                 'gemini_keys':['fixture-key'],'user_gemini_key':None,'get_limits':(100000,1000000),
                 'gemini_client':client,'openrouter_keys':[], 'plan_context':'','memory_prompt':'',
                 'time_prompt':'','live_view_digest':'','project_digest':'','_tool_digest':'',
                 '_route_turn':A.routing.Route('core','test',[A.DEFAULT_MODEL]),
                 '_model_chain':[A.DEFAULT_MODEL]}
        from contextlib import ExitStack
        with self.app.app_context(),ExitStack() as stack:
            for name,value in patches.items():stack.enter_context(patch.object(A,name,return_value=value))
            stack.enter_context(patch.object(A.KEY_MANAGER,'first_available',return_value=0))
            stack.enter_context(patch.object(A,'_run_tool_interruptibly',side_effect=tool))
            stack.enter_context(patch.object(A,'notify_user'))
            args={'chat_id':1,'user_id':1,'message':'Build and deploy an application','mode':'develop','_query_id':'long-build'}
            events=list(A.gemini_producer(MagicMock(),args))
            self.assertEqual(chat.send_message_stream.call_count,14)
            self.assertTrue(any(e.get('type')=='status' and 'checkpoint' in e.get('text','') for e in events))
            row=W.latest(A.get_db(),1,1);self.assertEqual(row['status'],'completed')
            self.assertEqual(row['steps'],14);self.assertEqual(events[-1]['text'],'Verified build complete.')

    def test_buffered_build_narration_does_not_disable_backup_completion(self):
        part=NS(thought=False,text='I am creating the project.',function_call=NS(name='repo_control',args={'action':'deploy','status':'Creating project'}))
        chunk=NS(candidates=[NS(content=NS(parts=[part]))],usage_metadata=None)
        chat=MagicMock()
        calls=[0]
        def stream(message):
            calls[0]+=1
            if calls[0]==1:return iter([chunk])
            raise RuntimeError('429 RESOURCE_EXHAUSTED quota perday')
        chat.send_message_stream.side_effect=stream
        client=MagicMock();client.chats.create.return_value=chat
        def backup(*args):
            A.g.build_run.evidence['projects']['abcdef123456']={'tests':True,'served':True,'verified':True}
            if False:yield {}
            return 'Backup verified the application.', 'fixture-backup'
        patches={'register_generation':threading.Event(),'is_stopped':False,'release_generation':None,
                 '_drain_injections':[],'_end_turn_if_quiet':True,'_finalize_once':None,
                 'gemini_keys':['fixture-key'],'user_gemini_key':None,'get_limits':(100000,1000000),
                 'gemini_client':client,'openrouter_keys':['fixture-backup-key'], 'plan_context':'','memory_prompt':'',
                 'time_prompt':'','live_view_digest':'','project_digest':'','_tool_digest':'',
                 '_route_turn':A.routing.Route('core','test',[A.DEFAULT_MODEL]),'_model_chain':[A.DEFAULT_MODEL]}
        from contextlib import ExitStack
        with self.app.app_context(),ExitStack() as stack:
            for name,value in patches.items():stack.enter_context(patch.object(A,name,return_value=value))
            stack.enter_context(patch.object(A.KEY_MANAGER,'first_available',return_value=0))
            stack.enter_context(patch.object(A.KEY_MANAGER,'short_retry_after',return_value=None))
            stack.enter_context(patch.object(A.KEY_MANAGER,'block'))
            stack.enter_context(patch.object(A,'_run_tool_interruptibly',return_value=('**Process ID**: `abcdef123456`',False)))
            stack.enter_context(patch.object(A,'_openrouter_turn',side_effect=backup))
            stack.enter_context(patch.object(A,'notify_user'))
            events=list(A.gemini_producer(MagicMock(),{'chat_id':1,'user_id':1,'message':'Build and deploy an app','mode':'develop','_query_id':'backup-build'}))
            self.assertEqual(events[-1]['text'],'Backup verified the application.')
            self.assertEqual(W.latest(A.get_db(),1,1)['status'],'completed')
            self.assertFalse(any(e.get('type')=='token' and e.get('text')=='I am creating the project.' for e in events))


if __name__=='__main__':unittest.main(verbosity=2)
