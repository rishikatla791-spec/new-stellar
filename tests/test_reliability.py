"""Offline regressions for tool honesty, deployments and HTTP contracts."""
import sys
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = A.create_app({"TESTING": True, "BACKGROUND_THREADS": False,
            "SECRET_KEY": "offline-audit-only", "CSRF_TOKENS": False,
            "DATABASE": str(root / "audit.db"), "DEPLOYMENTS_DIR": str(root / "apps"),
            "OUTPUTS_DIR": str(root / "outputs"), "SANDBOX_DIR": str(root / "labs")})
        with self.app.app_context():
            A.init_db()
            db = A.get_db()
            db.execute("INSERT INTO users(id,username,password_hash,is_approved) VALUES(1,'audit@example.com','',1)")
            db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Audit')")
            db.execute("INSERT INTO repo_history(user_id,project_name,process_id,subdomain,status) VALUES(1,'Audit app','audit-proj','audit-app','running')")
            db.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['epoch'] = 0

    def test_failed_shell_command_is_an_error(self):
        with self.app.app_context(), patch.object(A, '_get_or_create_lab'), patch.object(A, '_run_in_lab', return_value=(2, 'test failed')):
            result = A._lab_run(MagicMock(), 1, 1, 'pytest', 60, '')
            with patch.dict(A.TOOLS_BY_NAME, audit=lambda: result):
                text, failed = A._execute_tool('audit', {})
            self.assertTrue(failed)
            self.assertIn('Exit code 2', text)

    def test_unavailable_search_and_image_are_errors(self):
        with self.app.app_context(), patch.object(A, 'tavily_keys', return_value=[]):
            _, failed = A._execute_tool('web_search', {'query':'offline', 'status':'Searching'})
            self.assertTrue(failed)
            _, failed = A._execute_tool('generate_image', {'prompt':'test', 'status':'Drawing'})
            self.assertTrue(failed)

    def test_stop_failure_does_not_claim_success_or_change_state(self):
        with self.app.app_context(), patch.object(A, '_docker'), patch.object(A, '_perform_snapshot'), patch.object(A, '_sleep_project', side_effect=RuntimeError('daemon unavailable')):
            A.g.lab_user_id = A.g.lab_chat_id = 1
            _, failed = A._execute_tool('repo_control', {'action':'stop','app_id':'audit-proj','status':'Stopping'})
            self.assertTrue(failed)
            row = A.get_db().execute("SELECT status FROM repo_history WHERE process_id='audit-proj'").fetchone()
            self.assertEqual(row['status'], 'running')

    def test_clone_failure_is_not_a_successful_deployment(self):
        container = MagicMock()
        container.id = 'audit-container'
        container.exec_run.return_value = SimpleNamespace(exit_code=128, output=b'fatal: repository not found')
        with self.app.app_context(), patch.object(A, '_docker'), patch.object(A, 'quota_message', return_value=None), patch.object(A, '_make_room'), patch.object(A, '_start_repo_container', return_value=container), patch.object(A, '_published_port', return_value=40000), patch.object(A, '_git_ready') as ready, patch.object(A, '_checkpoint'), patch.object(A, '_project_touch'):
            A.g.lab_user_id = A.g.lab_chat_id = 1
            text, failed = A._execute_tool('repo_control', {'action':'deploy','repo_url':'https://example.com/missing.git','project_name':'Missing','status':'Deploying'})
            self.assertTrue(failed, text)
            ready.assert_not_called()
            self.assertEqual(A.get_db().execute("SELECT status FROM repo_history WHERE project_name='Missing'").fetchone()[0], 'failed')

    def test_serve_without_an_answer_is_an_error(self):
        with self.app.app_context(), patch.object(A, '_docker'), patch.object(A, 'quota_message', return_value=None), patch.object(A, '_ensure_project_running', return_value=MagicMock()), patch.object(A, '_write_start_script'), patch.object(A, '_relaunch_server'), patch.object(A, '_probe_port', return_value=None), patch.object(A, '_checkpoint'), patch.object(A, '_server_log_tail', return_value='boot failed'):
            A.g.lab_user_id = A.g.lab_chat_id = 1
            text, failed = A._execute_tool('repo_control', {'action':'serve','app_id':'audit-proj','command':'npm start','status':'Starting'})
            self.assertTrue(failed, text)

    def test_malformed_origin_is_rejected_without_exception(self):
        with self.app.test_request_context('/api/chats', method='POST', headers={'Origin':'https://['}):
            self.assertFalse(A._from_this_site())

    def test_api_errors_are_json_and_keep_http_headers(self):
        response = self.client.get('/api/does-not-exist')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.is_json)
        self.assertIn('no-store', response.headers['Cache-Control'])
        response = self.client.put('/api/chats')
        self.assertEqual(response.status_code, 405)
        self.assertIn('POST', response.headers['Allow'])
        self.assertTrue(response.is_json)

    def test_non_object_json_is_bad_request(self):
        for value in ([1], 'text', 42, True, None):
            response = self.client.post('/api/chats', json=value) if value is not None else self.client.post('/api/chats', data='null', content_type='application/json')
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.is_json)

    def test_tool_arguments_and_results_are_redacted_before_storage(self):
        secret = 'audit-fake-secret-with-"quotes"'
        with self.app.app_context(), patch.dict(os.environ, {'BACKUP_API_KEY_99':secret}):
            A._secret_values.cache_clear()
            try:
                row_id = A._record_tool_call(A.get_db(), 1, 'audit', {'nested':[secret, {'command':'echo '+secret}], 'count':2}, secret, 12, True)
                row = A.get_db().execute('SELECT * FROM tool_calls WHERE id=?', (row_id,)).fetchone()
                args = json.loads(row['arguments'])
                self.assertEqual(args['nested'][0], '[redacted]')
                self.assertEqual(args['count'], 2)
                self.assertEqual(row['result'], '[redacted]')
                self.assertEqual(row['is_error'], 1)
            finally:
                A._secret_values.cache_clear()

    def test_normal_output_containing_error_is_still_success(self):
        with patch.dict(A.TOOLS_BY_NAME, audit=lambda: '0 errors: all checks passed'):
            text, failed = A._execute_tool('audit', {})
            self.assertFalse(failed)
            self.assertIn('all checks passed', text)

    def test_internal_type_error_is_not_mislabeled_as_bad_arguments(self):
        def tool():
            raise TypeError('internal bug')
        with patch.dict(A.TOOLS_BY_NAME, audit=tool):
            text, failed = A._execute_tool('audit', {})
            self.assertTrue(failed)
            self.assertNotIn('Invalid arguments', text)
            text, failed = A._execute_tool('audit', {'unexpected':1})
            self.assertTrue(failed)
            self.assertIn('Invalid arguments', text)

    def test_origin_validation_preserves_host_and_rejects_bad_syntax(self):
        with patch.object(A, 'stellar_domain', return_value='app.example.com'):
            for origin, expected in [('https://app.example.com',True), ('null',False), ('https://app.example.com/path',False), ('https://user@app.example.com',False), ('https://evil.example.com',False)]:
                with self.app.test_request_context('/api/chats', method='POST', headers={'Origin':origin}):
                    self.assertEqual(A._from_this_site(), expected, origin)

    def test_successful_lab_output_is_preserved(self):
        with self.app.app_context(), patch.object(A, '_get_or_create_lab'), patch.object(A, '_run_in_lab', return_value=(0,'all good')):
            result = A._lab_run(MagicMock(), 1, 1, 'echo ok', 60, '')
            with patch.dict(A.TOOLS_BY_NAME, audit=lambda:result):
                text, failed = A._execute_tool('audit', {})
            self.assertEqual(text, 'all good')
            self.assertFalse(failed)

    def test_internal_http_errors_do_not_expose_details(self):
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        def crash():
            raise RuntimeError('private server details')
        self.app.add_url_rule('/api/audit-crash', view_func=crash)
        response = self.client.get('/api/audit-crash')
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertNotIn('private server details', response.get_data(as_text=True))

    def test_loaded_commit_handles_worktrees_and_packed_refs(self):
        root = Path(self.temp.name)
        repo = root / 'checkout'
        common = root / 'common'
        worktree = common / 'worktrees' / 'audit'
        repo.mkdir()
        worktree.mkdir(parents=True)
        (repo / '.git').write_text('gitdir: ../common/worktrees/audit\n')
        (worktree / 'commondir').write_text('../..\n')
        (worktree / 'HEAD').write_text('ref: refs/heads/main\n')
        digest = 'abcdef0123456789' * 2 + 'abcdef01'
        (common / 'packed-refs').write_text('# pack-refs\n'+digest+' refs/heads/main\n')
        with patch.object(A, '__file__', str(repo / 'app.py')):
            self.assertEqual(A._loaded_commit(), digest[:7])
            (worktree / 'HEAD').write_text(digest+'\n')
            self.assertEqual(A._loaded_commit(), digest[:7])
            (worktree / 'HEAD').write_text('not-a-commit\n')
            self.assertIsNone(A._loaded_commit())

    def test_chat_fields_reject_non_text_without_starting_work(self):
        with patch.object(A, 'rate_limited', return_value=False):
            for value in (42, [1], {'text':'hello'}, True):
                response = self.client.post('/api/chats/1/name', json={'name':value})
                self.assertEqual(response.status_code, 400)
                response = self.client.post('/api/chats/1/query', json={'message':value})
                self.assertEqual(response.status_code, 400)
            response = self.client.post('/api/chats/1/query', json={'message':'hello','attachment_ids':'12'})
            self.assertEqual(response.status_code, 400)

    def test_legacy_tool_previews_and_context_are_redacted(self):
        secret = 'audit-legacy-fake-secret'
        with self.app.app_context():
            db = A.get_db()
            message = db.execute("INSERT INTO messages(chat_id,message_type,message_content) VALUES(1,'stellar','Audit reply')").lastrowid
            db.execute("INSERT INTO tool_calls(chat_id,message_id,tool_name,arguments,result,duration_ms,is_error) VALUES(1,?,'audit',?,?,1,1)", (message,json.dumps({'status':secret}),secret))
            db.commit()
        with patch.dict(os.environ, {'BACKUP_API_KEY_99':secret}):
            A._secret_values.cache_clear()
            try:
                with self.app.app_context():
                    digest = A._tool_digest(A.get_db(), 1)
                    self.assertNotIn(secret, digest)
                    self.assertIn('[redacted]', digest)
                response = self.client.get('/api/chats/1/messages')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()[0]['tools'][0]['preview'], '[redacted]')
            finally:
                A._secret_values.cache_clear()


if __name__ == '__main__':
    unittest.main()
