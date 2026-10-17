"""Flask ownership, CSRF, feature-gate and browser evidence contracts."""
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A
import browser_checks
import test_reliability as reliability


class PreviewAppTests(unittest.TestCase):
    setUp = reliability.ReliabilityTests.setUp

    def configured(self):
        self.app.config.update(STELLAR_APP_ORIGIN="https://stellar.example.org",
            STELLAR_PREVIEW_ORIGIN="https://preview.example.net", BROWSER_CHECKS_ENABLED=True)
        with self.app.app_context():
            A.get_db().execute("UPDATE repo_history SET process_id='abcdef123456'")
            A.get_db().commit()

    def test_disabled_preview_does_not_break_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-src 'self';", response.headers["Content-Security-Policy"])
        self.assertFalse(self.client.get("/api/preview/config").json["enabled"])

    def test_preview_csp_is_narrow(self):
        self.configured()
        response = self.client.get("/")
        self.assertIn("frame-src 'self' https://*.preview.example.net;", response.headers["Content-Security-Policy"])
        self.assertNotIn("frame-src *", response.headers["Content-Security-Policy"])

    def test_launch_checks_ownership_state_and_csrf(self):
        self.configured()
        self.assertEqual(self.client.post("/api/projects/000000000000/preview", json={}).status_code, 404)
        self.app.config["CSRF_TOKENS"] = True
        self.assertEqual(self.client.post("/api/projects/abcdef123456/preview", json={}).status_code, 403)
        self.app.config["CSRF_TOKENS"] = False
        with self.app.app_context():
            A.get_db().execute("UPDATE repo_history SET status='stopped'")
            A.get_db().commit()
        with patch.object(A, "_project_action_limited", return_value=False):
            self.assertEqual(self.client.post("/api/projects/abcdef123456/preview", json={}).status_code, 409)

    def test_launch_dependency_failure_and_success(self):
        self.configured()
        with patch.object(A, "_project_action_limited", return_value=False), patch.object(A, "_deployment_route", return_value=12345), patch.object(A, "_redis_client") as redis:
            response = self.client.post("/api/projects/abcdef123456/preview", json={})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["launch_url"].startswith("https://p-abcdef123456.preview.example.net/__stellar_preview/launch?ticket="))
            self.assertIn("no-store", response.headers["Cache-Control"])
            redis.return_value.setex.assert_called_once()
            redis.side_effect = RuntimeError("private details")
            response = self.client.post("/api/projects/abcdef123456/preview", json={})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private details", response.json["error"])

    def test_signout_and_unapproved_are_rejected(self):
        self.configured()
        with self.app.app_context():
            A.get_db().execute("UPDATE users SET is_approved=0")
            A.get_db().commit()
        self.assertEqual(self.client.post("/api/projects/abcdef123456/preview", json={}).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/preview/config").status_code, 401)

    def test_browser_unavailable_and_invalid_arguments_fail_explicitly(self):
        with self.app.app_context():
            result = A.browser_test("abcdef123456", "Checking")
            self.assertIsInstance(result, A.ToolFailure)
        self.configured()
        with self.app.app_context():
            A.g.lab_user_id = A.g.lab_chat_id = 1
            for steps in ("not json", '[{"action":"evaluate","script":"alert(1)"}]'):
                self.assertIsInstance(A.browser_test("abcdef123456", "Checking", steps), A.ToolFailure)
        response = self.client.post("/api/projects/abcdef123456/browser-check", json={"chat_id": "1"})
        self.assertEqual(response.status_code, 400)

    def test_browser_report_and_failed_assertion_evidence(self):
        self.configured()
        with self.app.app_context(), patch.object(A, "rate_limited", return_value=False), patch.object(A, "quota_message", return_value=None), patch.object(A, "_redis_client"), patch.object(browser_checks, "docker_client") as docker, patch.object(browser_checks, "run_check") as run:
            A.g.lab_user_id = A.g.lab_chat_id = 1
            target = docker.return_value.containers.get.return_value
            target.status = "running"
            target.labels = {"stellar": "repo", "user": "1", "process_id": "abcdef123456"}
            for passed in (True, False):
                run.return_value = {"passed": passed, "screenshot_base64": base64.b64encode(b"jpeg-test").decode()}
                result = A.browser_test("abcdef123456", "Checking")
                self.assertEqual(isinstance(result, A.ToolFailure), not passed)
                report = json.loads(result)
                self.assertIn("/api/outputs/1/browser-", report["report"])
                self.assertNotIn("screenshot_base64", report)
                self.assertTrue((A._outputs_dir(1, 1) / report["screenshot"].rsplit("/", 1)[1]).exists())
            target.labels["user"] = "2"
            self.assertIsInstance(A.browser_test("abcdef123456", "Checking"), A.ToolFailure)

    def test_tool_mode_boundary(self):
        self.assertIn("browser_test", A.turn_tool_names("develop"))
        self.assertNotIn("browser_test", A.turn_tool_names("plan"))
        self.assertNotIn("browser_test", A.turn_tool_names("chat"))


if __name__ == "__main__":
    unittest.main()
