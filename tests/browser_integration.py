"""Real Chromium fixtures. Requires Docker and the built stellar-browser image."""
import base64
from pathlib import Path
import secrets
import sys
import time

import docker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import run_check

SERVER = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location','https://example.com/'); self.end_headers(); return
        if self.path == '/metadata':
            self.send_response(302); self.send_header('Location','http://169.254.169.254/latest/meta-data/'); self.end_headers(); return
        status = 404 if self.path == '/missing' else 200
        script = {
            '/error': 'console.error("deliberate console error")',
            '/network': 'fetch("/missing")',
            '/external': 'fetch("https://example.com/private")',
        }.get(self.path, '')
        body = ('<!doctype html><title>Fixture</title><h1>Browser fixture</h1>'
                '<label>Name<input id="name"></label><button id="go" '
                'onclick="document.getElementById(\'result\').textContent=document.getElementById(\'name\').value">Go</button>'
                '<p id="result"></p><script>'+script+'</script>').encode()
        self.send_response(status); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0',5000),Handler).serve_forever()
'''


def main():
    client = docker.from_env(timeout=10)
    name = "stellar-browser-fixture-" + secrets.token_hex(6)
    before_containers = {c.id for c in client.containers.list(all=True, filters={"label": "stellar=browser-check"})}
    before_networks = {n.id for n in client.networks.list(filters={"label": "stellar=browser-check"})}
    target = client.containers.run("stellar-browser:1.63.0",
        command=["python", "-u", "-c", SERVER], name=name, detach=True,
        labels={"stellar": "preview-fixture"})
    try:
        for _ in range(30):
            result = target.exec_run(["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/')"])
            if result.exit_code == 0: break
            time.sleep(.2)
        else:
            raise AssertionError("Fixture did not start")
        steps = [{"action": "expect_title", "text": "Fixture"},
                 {"action": "fill", "selector": "#name", "value": "Stellar"},
                 {"action": "click", "selector": "#go"},
                 {"action": "expect_text", "text": "Stellar"}]
        cases = [("desktop", "/", steps, False, True),
                 ("mobile", "/", steps, True, True),
                 ("assertion", "/", [{"action": "expect_visible", "selector": "#does-not-exist"}], False, False),
                 ("console", "/error", [], False, False),
                 ("HTTP failure", "/network", [], False, False),
                 ("external request", "/external", [], False, False),
                 ("external redirect", "/redirect", [], False, False),
                 ("metadata redirect", "/metadata", [], False, False)]
        for label, path, actions, mobile, expected in cases:
            report = run_check(client, target, 5000, actions, path, mobile)
            details = {k: v for k, v in report.items() if k != "screenshot_base64"}
            assert report["passed"] is expected, (label, details)
            if label != "external redirect":
                assert base64.b64decode(report["screenshot_base64"]).startswith(b"\xff\xd8"), label
            if label.startswith("external") or label == "metadata redirect":
                assert report["blocked_requests"], details
            print("PASS", label, flush=True)
    finally:
        target.remove(force=True)
    after_containers = {c.id for c in client.containers.list(all=True, filters={"label": "stellar=browser-check"})}
    after_networks = {n.id for n in client.networks.list(filters={"label": "stellar=browser-check"})}
    assert after_containers == before_containers, "Browser container leak"
    assert after_networks == before_networks, "Browser network leak"
    print("PASS disposable container and network cleanup")


if __name__ == "__main__":
    main()
