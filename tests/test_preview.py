"""Preview capability, authorization and real HTTP/WebSocket proxy regressions."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preview_access import PreviewConfig, issue_ticket, consume_ticket, origin
from browser_checks import validate_path, validate_steps, run_check, cleanup_stale_runs


class FakeRedis:
    def __init__(self):
        self.data = {}
    def setex(self, key, ttl, value):
        self.data[key] = value
    def getdel(self, key):
        return self.data.pop(key, None)


class Capabilities(unittest.TestCase):
    def setUp(self):
        self.config = PreviewConfig.from_mapping({"DATABASE": "audit.db",
            "STELLAR_APP_ORIGIN": "https://stellar.example.org",
            "STELLAR_PREVIEW_ORIGIN": "https://preview.example.net"})

    def test_invalid_origins_fail_closed(self):
        for value in (None, "", "http://public.example", "https://x/a", "https://u:p@x",
                      "https://x?evil=1", "https://x#evil", "https://[", "javascript:alert(1)",
                      "https://x:99999", "https://*.x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                origin(value)

    def test_overlapping_domains_are_rejected(self):
        for host in ("stellar.example.org", "child.stellar.example.org", "example.org"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                PreviewConfig.from_mapping({"DATABASE": "audit.db",
                    "STELLAR_APP_ORIGIN": "https://stellar.example.org",
                    "STELLAR_PREVIEW_ORIGIN": "https://" + host})

    def test_single_use_capability_and_scope(self):
        r = FakeRedis()
        link = issue_ticket(r, self.config, 1, "abcdef123456", 3)
        token = parse_qs(urlsplit(link).query)["ticket"][0]
        self.assertEqual(consume_ticket(r, self.config, token),
                         {"user_id": 1, "project_id": "abcdef123456", "epoch": 3})
        self.assertIsNone(consume_ticket(r, self.config, token))
        self.assertIsNone(consume_ticket(r, self.config, "../../bad"))

    def test_invalid_actions_and_paths(self):
        for steps in ({}, [None], [{"action": "evaluate", "script": "alert(1)"}],
                      [{"action": "click", "selector": ""}], [{"action": "expect_text"}],
                      [{"action": "fill", "selector": "input", "value": 3}],
                      [{"action": "expect_text", "text": "x"}] * 13):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                validate_steps(steps)
        for path in ("https://evil.example", "//evil", "/\\evil", "/\nheader", "", None, "/#anchor"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_path(path)
        self.assertEqual(validate_path("/settings?q=test"), "/settings?q=test")
        validate_steps([{"action": "fill", "selector": "#name", "value": "Hello"}])

    def test_browser_timeout_and_start_failure_cleanup(self):
        for fail_at in ("wait", "start"):
            with self.subTest(fail_at=fail_at):
                client, target = MagicMock(), MagicMock()
                network = client.networks.create.return_value
                if fail_at == "wait":
                    client.containers.run.return_value.wait.side_effect = TimeoutError()
                else:
                    client.containers.run.side_effect = RuntimeError("missing image")
                with self.assertRaises(Exception):
                    run_check(client, target, 5000, [], "/")
                network.disconnect.assert_called_once_with(target, force=True)
                network.remove.assert_called_once()
                if fail_at == "wait":
                    client.containers.run.return_value.remove.assert_called_once_with(force=True)

    def test_uncertain_container_creation_is_recovered_by_run_label(self):
        client, target = MagicMock(), MagicMock()
        client.containers.run.side_effect = TimeoutError()
        candidate = client.containers.get.return_value
        candidate.labels = {"run": "1" * 16}
        with patch("browser_checks.secrets.token_hex", return_value="1" * 16), self.assertRaises(TimeoutError):
            run_check(client, target, 5000, [], "/")
        candidate.remove.assert_called_once_with(force=True)
        client.networks.create.return_value.remove.assert_called_once()

    def test_stale_reaper_refreshes_endpoints_and_preserves_current_runs(self):
        client = MagicMock()
        old, current = MagicMock(), MagicMock()
        old.labels = {"expires": "1"}
        current.labels = {"expires": "9999999999"}
        client.containers.list.return_value = [old, current]
        network = MagicMock()
        network.attrs = {"Labels": {"expires": "1"}, "Containers": {"app-container-id": {}}}
        client.networks.list.return_value = [network]
        cleanup_stale_runs(client, "audit")
        old.remove.assert_called_once_with(force=True)
        current.remove.assert_not_called()
        network.reload.assert_called_once()
        network.disconnect.assert_called_once_with("app-container-id", force=True)
        network.remove.assert_called_once()


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from deploy.preview.gateway import Gateway
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "gateway.db")
        with sqlite3.connect(self.db) as db:
            db.executescript("CREATE TABLE users(id INTEGER,is_approved INTEGER,session_epoch INTEGER);"
                "CREATE TABLE repo_history(user_id INTEGER,process_id TEXT,status TEXT);"
                "INSERT INTO users VALUES(1,1,0);"
                "INSERT INTO repo_history VALUES(1,'abcdef123456','running');")
        upstream = web.Application()
        async def echo(request):
            return web.json_response({"body": (await request.read()).decode(),
                "cookie": request.headers.get("Cookie"), "auth": request.headers.get("Authorization"),
                "host": request.headers.get("X-Forwarded-Host")}, headers={
                "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
                "Access-Control-Allow-Origin": "https://evil.example", "Access-Control-Allow-Credentials": "true"})
        async def socket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == web.WSMsgType.TEXT:
                    await ws.send_str(message.data)
                elif message.type == web.WSMsgType.BINARY:
                    await ws.send_bytes(message.data)
            return ws
        async def stream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            await asyncio.sleep(.01)
            await response.write(b"data: second\n\n")
            return response
        async def cookies(request):
            response = web.Response(text="ok")
            response.headers.add("Set-Cookie", "app_login=ok; Path=/; HttpOnly")
            response.headers.add("Set-Cookie", "evil=1; Domain=example.net")
            response.headers.add("Set-Cookie", "__Host-stellar_preview=evil; Path=/; Secure")
            return response
        upstream.router.add_route("*", "/echo", echo)
        upstream.router.add_get("/ws", socket)
        upstream.router.add_get("/stream", stream)
        upstream.router.add_get("/cookies", cookies)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        class AsyncRedis:
            def __init__(self): self.data = {}
            async def getdel(self, key): return self.data.pop(key, None)
            async def get(self, key): return self.data.get(key)
            async def setex(self, key, ttl, value): self.data[key] = value
        self.redis = AsyncRedis()
        config = {"DATABASE": self.db, "STELLAR_APP_ORIGIN": "https://stellar.example.org",
                  "STELLAR_PREVIEW_ORIGIN": "https://preview.example.net"}
        self.docker = MagicMock()
        self.container = self.docker.containers.get.return_value
        self.container.status = "running"
        self.container.labels = {"stellar": "repo", "user": "1", "process_id": "abcdef123456"}
        self.container.attrs = {"NetworkSettings": {"Ports": {"5000/tcp": [
            {"HostIp": "127.0.0.1", "HostPort": str(self.upstream.port)}]}}}
        self.gateway = Gateway(config, self.redis, self.docker)
        self.client = TestClient(TestServer(self.gateway.application()))
        await self.client.start_server()
        self.host = "p-abcdef123456.preview.example.net"
        self.token = "a" * 43
        self.payload = {"user_id": 1, "project_id": "abcdef123456", "epoch": 0}
        await self.redis.setex(self.gateway.config.key("session", self.token), 900, json.dumps(self.payload))
        self.headers = {"Host": self.host, "Cookie": "__Host-stellar_preview=" + self.token}

    async def asyncTearDown(self):
        await self.client.close()
        await self.upstream.close()
        self.temp.cleanup()

    async def test_launch_is_single_use_secure_and_no_store(self):
        key = self.gateway.config.key("ticket", self.token)
        await self.redis.setex(key, 45, json.dumps(self.payload))
        response = await self.client.get("/__stellar_preview/launch?ticket=" + self.token,
            headers={"Host": self.host}, allow_redirects=False)
        self.assertEqual(response.status, 302)
        cookie = response.cookies["__Host-stellar_preview"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertFalse(cookie["domain"])
        self.assertEqual(response.headers["Location"], "/")
        self.assertIn("no-store", response.headers["Cache-Control"])
        replay = await self.client.get("/__stellar_preview/launch?ticket=" + self.token, headers={"Host": self.host})
        self.assertEqual(replay.status, 401)

    async def test_http_stream_and_credentials(self):
        headers = {**self.headers, "Origin": "https://" + self.host, "Authorization": "Bearer private", "Cookie": self.headers["Cookie"] + "; stellar_session_main=secret; app_theme=dark"}
        response = await self.client.post("/echo", data="hello", headers=headers)
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["body"], "hello")
        self.assertEqual(data["cookie"], "app_theme=dark")
        self.assertEqual(data["auth"], "Bearer private")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertNotIn("X-Frame-Options", response.headers)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors https://stellar.example.org", response.headers["Content-Security-Policy"])
        stream = await self.client.get("/stream", headers=self.headers)
        self.assertEqual(await stream.text(), "data: first\n\ndata: second\n\n")

    async def test_cookie_boundaries(self):
        response = await self.client.get("/cookies", headers=self.headers)
        self.assertEqual(response.headers.getall("Set-Cookie"), ["app_login=ok; Path=/; HttpOnly"])

    async def test_unauthorized_expired_host_and_revocation(self):
        response = await self.client.get("/echo", headers={"Host": self.host})
        self.assertEqual(response.status, 401)
        response = await self.client.get("/echo", headers={**self.headers, "Host": "p-000000000000.preview.example.net"})
        self.assertEqual(response.status, 403)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE users SET session_epoch=1")
        response = await self.client.get("/echo", headers=self.headers)
        self.assertEqual(response.status, 403)

    async def test_stopped_or_replaced_container_is_not_routed(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE repo_history SET status='stopped'")
        self.assertEqual((await self.client.get("/echo", headers=self.headers)).status, 409)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE repo_history SET status='running'")
        self.container.labels["user"] = "2"
        self.assertEqual((await self.client.get("/echo", headers=self.headers)).status, 503)

    async def test_busy_and_dependency_failure(self):
        self.gateway.active = 32
        self.assertEqual((await self.client.get("/echo", headers=self.headers)).status, 429)
        self.gateway.active = 0
        self.docker.containers.get.side_effect = RuntimeError("private socket path")
        response = await self.client.get("/echo", headers=self.headers)
        self.assertEqual(response.status, 503)
        self.assertNotIn("private socket", await response.text())

    async def test_upload_limits_csrf_and_upstream_failure_release_slots(self):
        self.assertEqual((await self.client.get("/echo", headers={**self.headers, "Service-Worker": "script"})).status, 403)
        response = await self.client.post("/echo", data="forged", headers=self.headers)
        self.assertEqual(response.status, 403)
        response = await self.client.post("/echo", data=b"x" * (8 * 1024 * 1024 + 1),
            headers={**self.headers, "Origin": "https://" + self.host})
        self.assertEqual(response.status, 413)
        self.assertEqual(self.gateway.active, 0)
        self.container.attrs["NetworkSettings"]["Ports"]["5000/tcp"][0]["HostPort"] = "1"
        response = await self.client.get("/echo", headers=self.headers)
        self.assertEqual(response.status, 502)
        self.assertEqual(self.gateway.active, 0)
        self.assertEqual(self.gateway.projects, {})

    async def test_live_socket_loses_access_when_session_expires(self):
        from aiohttp import WSMsgType
        with patch("deploy.preview.gateway.ACCESS_POLL_SECONDS", .02):
            socket = await self.client.ws_connect("/ws", headers={**self.headers, "Origin": "https://" + self.host})
            self.redis.data.pop(self.gateway.config.key("session", self.token))
            message = await socket.receive(timeout=3)
            self.assertIn(message.type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
            await socket.close()
            await asyncio.sleep(.05)
            self.assertEqual(self.gateway.active, 0)

    async def test_account_ownership_and_approval_are_checked_again(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE users SET is_approved=0")
        self.assertEqual((await self.client.get("/echo", headers=self.headers)).status, 403)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE users SET is_approved=1")
            db.execute("UPDATE repo_history SET user_id=2")
        self.assertEqual((await self.client.get("/echo", headers=self.headers)).status, 403)

    async def test_websocket_echo_and_origin(self):
        from aiohttp import WSServerHandshakeError
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect("/ws", headers={**self.headers, "Origin": "https://evil.example"})
        socket = await self.client.ws_connect("/ws", headers={**self.headers, "Origin": "https://" + self.host})
        await socket.send_str("hello")
        self.assertEqual((await socket.receive(timeout=3)).data, "hello")
        await socket.send_bytes(b"binary")
        self.assertEqual((await socket.receive(timeout=3)).data, b"binary")
        await socket.close()
        await asyncio.sleep(.05)
        self.assertEqual(self.gateway.active, 0)
        self.assertEqual(self.gateway.projects, {})


if __name__ == "__main__":
    unittest.main()
