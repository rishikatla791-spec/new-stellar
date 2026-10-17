"""Fixed Playwright program inside the isolated browser image."""
import base64
import json
import sys
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import select
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


def scoped_proxy(origin, blocked):
    """Enforce the origin at the network hop, including redirected requests.

    Playwright routing alone only intercepts the first URL of a redirect.
    The browser also runs on an internal Docker network, without host access.
    """
    target = urlsplit(origin)
    hop = {"connection", "proxy-connection", "proxy-authorization", "keep-alive", "transfer-encoding"}

    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def do_CONNECT(self):
            blocked("https://" + self.path)
            self.send_error(403, "External connections are disabled")
        def forward(self):
            parsed = urlsplit(self.path)
            if (parsed.scheme != target.scheme or parsed.netloc != target.netloc
                    or parsed.username or parsed.password):
                blocked(self.path)
                self.send_error(403, "External connections are disabled")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 8 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                    self.send_error(413)
                    return
                upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
                headers = {k: v for k, v in self.headers.items() if k.lower() not in hop}
                headers["Connection"] = "Upgrade" if upgrade else "close"
                conn = http.client.HTTPConnection(target.hostname, target.port, timeout=15)
                path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                conn.request(self.command, path, body=self.rfile.read(size), headers=headers)
                response = conn.getresponse()
                self.send_response_only(response.status, response.reason)
                for name, value in response.getheaders():
                    if name.lower() not in hop:
                        self.send_header(name, value)
                self.send_header("Connection", "Upgrade" if response.status == 101 else "close")
                self.end_headers()
                if response.status == 101:
                    deadline = time.monotonic() + 90
                    sockets = [self.connection, conn.sock]
                    while time.monotonic() < deadline:
                        readable, _, _ = select.select(sockets, [], [], 1)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (sockets[1] if source is sockets[0] else sockets[0]).sendall(data)
                elif self.command != "HEAD":
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
            except Exception:
                self.close_connection = True
            finally:
                if "conn" in locals():
                    conn.close()
                self.close_connection = True
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = forward
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run(spec):
    report = {"passed": False, "assertions": [], "console_errors": [],
              "http_errors": [], "request_failures": [], "blocked_requests": []}
    origin = spec["origin"]
    with sync_playwright() as playwright:
        proxy = scoped_proxy(origin, lambda url: report["blocked_requests"].append(str(url)[:500])
            if len(report["blocked_requests"]) < 30 else None)
        browser = playwright.chromium.launch(proxy={"server": f"http://127.0.0.1:{proxy.server_port}",
                                                   "bypass": "<-loopback>"})
        context = browser.new_context(
            viewport={"width": 390 if spec["mobile"] else 1280, "height": 844 if spec["mobile"] else 800},
            is_mobile=spec["mobile"], device_scale_factor=1,
            service_workers="block", accept_downloads=False)
        context.set_default_timeout(4000)
        context.set_default_navigation_timeout(15000)

        def bounded(key, value):
            if len(report[key]) < 30:
                report[key].append(str(value)[:500])

        def allow(route):
            parsed = urlsplit(route.request.url)
            if f"{parsed.scheme}://{parsed.netloc}" != origin:
                bounded("blocked_requests", route.request.url)
                route.abort("blockedbyclient")
            else:
                route.continue_()
        context.route("**/*", allow)
        def allow_socket(ws):
            parsed = urlsplit(ws.url)
            if f"{parsed.scheme}://{parsed.netloc}" == origin.replace("http:", "ws:"):
                ws.connect_to_server()
            else:
                bounded("blocked_requests", ws.url)
                ws.close()
        context.route_web_socket("**/*", allow_socket)
        page = context.new_page()
        page.on("console", lambda msg: bounded("console_errors", msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: bounded("console_errors", exc))
        page.on("response", lambda response: bounded("http_errors", f"{response.status} {response.url}") if response.status >= 400 else None)
        page.on("requestfailed", lambda request: bounded("request_failures", f"{request.url}: {request.failure}"))
        context.on("page", lambda popup: popup.close() if popup != page else None)
        page.on("dialog", lambda dialog: dialog.dismiss())
        try:
            response = page.goto(origin + spec["path"], wait_until="domcontentloaded")
            report["status"] = response.status if response else None
            for index, step in enumerate(spec["steps"]):
                action = step["action"]
                if action == "click":
                    page.locator(step["selector"]).click()
                elif action == "fill":
                    page.locator(step["selector"]).fill(step["value"])
                elif action == "expect_text":
                    expect(page.locator("body")).to_contain_text(step["text"])
                elif action == "expect_visible":
                    expect(page.locator(step["selector"])).to_be_visible()
                elif action == "expect_title":
                    expect(page).to_have_title(step["text"])
                report["assertions"].append({"step": index + 1, "action": action, "passed": True})
            # Collect immediate effects; do not wait for network-idle (HMR/SSE
            # applications may keep connections open forever).
            page.wait_for_timeout(250)
            report["title"] = page.title()[:300]
            report["text"] = page.locator("body").inner_text(timeout=2000)[:6000]
            report["passed"] = not any(report[k] for k in (
                "console_errors", "http_errors", "request_failures", "blocked_requests"))
        except Exception as exc:
            report["error"] = str(exc)[:2000]
        finally:
            try:
                picture = page.screenshot(type="jpeg", quality=70, timeout=5000)
                if len(picture) <= 1024 * 1024:
                    report["screenshot_base64"] = base64.b64encode(picture).decode()
            except Exception:
                report["screenshot_error"] = "Screenshot unavailable."
            context.close()
            browser.close()
            proxy.shutdown()
            proxy.server_close()
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(run(json.loads(base64.b64decode(sys.argv[1])))))
    except Exception:
        print(json.dumps({"passed": False, "error": "Browser could not start or complete the check."}))
        sys.exit(1)
