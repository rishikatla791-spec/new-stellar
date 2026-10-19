"""Operations status must be honest, private and useful during degraded hosting."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A
PROBE = A._dependency_checks


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = A.create_app({'TESTING': True, 'BACKGROUND_THREADS': False, 'SECRET_KEY': 'operations-test',
            'DATABASE': str(root / 'test.db'), 'CSRF_TOKENS': False,
            'OUTPUTS_DIR': str(root / 'outputs'), 'DEPLOYMENTS_DIR': str(root / 'deployments'),
            'SANDBOX_DIR': str(root / 'labs'), 'STELLAR_APP_ORIGIN': 'https://app.example.org',
            'STELLAR_PREVIEW_ORIGIN': 'https://preview.example.net', 'BROWSER_CHECKS_ENABLED': True})
        with self.app.app_context():
            A.init_db()
            db = A.get_db()
            db.executemany('INSERT INTO users(id,username,password_hash,is_approved,is_admin) VALUES(?,?,?,?,?)',
                           [(1, 'admin@example.org', '', 1, 1), (2, 'member@example.org', '', 1, 0), (3, 'pending@example.org', '', 0, 0)])
            db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Private chat')")
            db.execute("INSERT INTO tool_calls(chat_id,tool_name,arguments,result,duration_ms,is_error) VALUES(1,'repo_control',?,?,25,1)",
                       ('{"password":"never-return-this"}', 'private transcript never-return-this'))
            db.commit()
        self.client = self.app.test_client()
        self.signin(1)
        patches = [patch.object(A, '_dependency_checks', return_value={'database': 'ok', 'redis': 'ok', 'docker': 'ok'}),
                   patch.object(A, 'user_gemini_key', return_value='gemini-private-test-key'),
                   patch.object(A, 'shared_gemini_keys', return_value=[]),
                   patch.object(A, 'openrouter_keys', return_value=[]),
                   patch.object(A, 'tavily_keys', return_value=[]),
                   patch.object(A, 'user_disk_usage', return_value=10), patch.object(A, 'disk_quota', return_value=100),
                   patch.object(A.KEY_MANAGER, 'status', return_value=[{'usable': True}])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def signin(self, uid):
        with self.client.session_transaction() as session:
            session.clear()
            if uid:
                session['user_id'] = uid
                session['epoch'] = 0

    def test_personal_key_is_counted_without_exposing_it(self):
        response = self.client.get('/api/admin/health')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['status'], 'operational')
        self.assertTrue(data['provider']['personal_key'])
        self.assertEqual(data['provider']['shared_keys'], 0)
        self.assertTrue(data['features']['chat']['available'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn('gemini-private-test-key', response.get_data(as_text=True))

    def test_docker_outage_keeps_chat_and_explains_container_limits(self):
        A._dependency_checks.return_value['docker'] = 'down'
        data = self.client.get('/api/runtime').get_json()
        self.assertEqual(data['status'], 'limited')
        self.assertTrue(data['features']['chat']['available'])
        for feature in ('terminal', 'code', 'deploy', 'preview', 'browser'):
            self.assertFalse(data['features'][feature]['available'])
            self.assertIn('runner', data['features'][feature]['reason'])

    def test_core_outage_is_not_reported_as_online(self):
        A._dependency_checks.return_value['redis'] = 'down'
        data = self.client.get('/api/runtime').get_json()
        self.assertEqual(data['status'], 'unavailable')
        self.assertFalse(data['features']['chat']['available'])
        self.assertIsNone(data['provider']['usable_keys'])

    def test_quota_block_and_storage_block_are_reported(self):
        A.KEY_MANAGER.status.return_value = [{'usable': False}]
        A.user_disk_usage.return_value = 101
        data = self.client.get('/api/runtime').get_json()
        self.assertTrue(data['provider']['quota_blocked'])
        self.assertTrue(data['storage']['blocked'])
        self.assertFalse(data['features']['chat']['available'])
        self.assertFalse(data['features']['deploy']['available'])

    def test_backup_provider_can_keep_chat_available(self):
        A.KEY_MANAGER.status.return_value = [{'usable': False}]
        A.openrouter_keys.return_value = ['private-backup-key']
        data = self.client.get('/api/runtime').get_json()
        self.assertTrue(data['features']['chat']['available'])
        self.assertFalse(data['provider']['quota_blocked'])
        self.assertNotIn('private-backup-key', json.dumps(data))

    def test_missing_provider_and_preview_have_actionable_messages(self):
        A.user_gemini_key.return_value = None
        self.app.config['STELLAR_PREVIEW_ORIGIN'] = ''
        data = self.client.get('/api/runtime').get_json()
        self.assertFalse(data['features']['chat']['available'])
        self.assertIn('Settings', data['features']['chat']['reason'])
        self.assertFalse(data['features']['preview']['available'])
        self.assertIn('HTTPS', data['features']['preview']['reason'])

    def test_browser_checks_do_not_require_public_preview_dns(self):
        self.app.config['STELLAR_PREVIEW_ORIGIN'] = ''
        data = self.client.get('/api/runtime').get_json()
        self.assertFalse(data['features']['preview']['available'])
        self.assertTrue(data['features']['browser']['available'])
        self.assertIn('browser image',data['features']['browser']['reason'])

    def test_failure_feed_excludes_arguments_and_results(self):
        response = self.client.get('/api/admin/health')
        data = response.get_json()
        self.assertEqual(data['tool_activity']['failed'], 1)
        self.assertEqual(data['recent_failures'][0]['tool_name'], 'repo_control')
        self.assertNotIn('never-return-this', response.get_data(as_text=True))
        self.assertNotIn('arguments', data['recent_failures'][0])
        self.assertNotIn('result', data['recent_failures'][0])

    def test_runtime_needs_approval_and_operations_need_admin(self):
        self.signin(2)
        self.assertEqual(self.client.get('/api/runtime').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/health').status_code, 403)
        self.signin(3)
        self.assertEqual(self.client.get('/api/runtime').status_code, 403)
        self.signin(None)
        self.assertEqual(self.client.get('/api/runtime').status_code, 401)

    def test_unknown_provider_quota_does_not_crash_dashboard(self):
        A.KEY_MANAGER.status.side_effect = RuntimeError('private backend failure')
        data = self.client.get('/api/admin/health').get_json()
        self.assertIsNone(data['provider']['usable_keys'])
        self.assertNotIn('private backend failure', json.dumps(data))

    def test_probe_clients_close_after_failures(self):
        connection, docker_client = MagicMock(), MagicMock()
        connection.ping.side_effect = RuntimeError('private redis password')
        docker_client.ping.side_effect = RuntimeError('private docker endpoint')
        with self.app.app_context(), patch('redis.Redis.from_url', return_value=connection), patch('docker.from_env', return_value=docker_client):
            checks = PROBE()
        self.assertEqual(checks['redis'], 'down')
        self.assertEqual(checks['docker'], 'down')
        connection.close.assert_called_once()
        docker_client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
