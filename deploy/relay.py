"""Serve new-stellar.stellarai.site from a machine that has Docker (the
runner), through the copy that lives inside main Stellar (the front).

    python3 deploy/relay.py front     # inside main Stellar; auto_pull starts it
    python3 deploy/relay.py runner    # on the runner; a systemd service there

Why: main Stellar gives the projects it hosts no Docker, so inside it the
labs, terminals, code runs and deployed apps cannot work. And nothing can
reach into that container except main Stellar's own proxy, which forwards
plain HTTP only (no WebSockets, and it drops the Host header). So the
connection has to go outward from the container, to the machine with
Docker:

  visitor -> Cloudflare -> main Stellar's proxy -> front (this file, :5000)
          -> WebSocket over HTTPS -> Cloudflare quick tunnel -> runner
          -> Stellar's own Gunicorn on the runner (127.0.0.1:5000) -> Docker

The runner opens a Cloudflare quick tunnel (an https://*.trycloudflare.com
address; no account, nothing opened on its network) and tells the front
where it is, every 30 seconds. The front then forwards every request to it
over WebSockets: one request per exchange, connections reused.

  front -> runner   text   {"m": method, "p": path, "h": [[name, value], ...]}
                    bytes  the body in pieces, then an empty bytes message
  runner -> front   text   {"s": status, "r": reason, "h": [[name, value], ...]}
                    bytes  the body as it comes (chat streams stay streams)
                    text   {"end": true}            or {"err": "..."}

Every WebSocket opens with the header X-Relay-Auth: "<unix time>.<hmac>", an
HMAC-SHA256 under RELAY_SECRET (in keys.env on both machines); announce and
export calls are signed the same way. The runner forwards only to its own
Stellar on 127.0.0.1, nothing else.

When the runner is away (laptop off), the front serves the landing pages
from its own Gunicorn (on 127.0.0.1:5002, cookies removed, so an old
session can never show stale data) and answers everything else with an
"offline" page. RELAY_FALLBACK=full serves everything from the front's own
copy instead.

Moving in: the first time the runner finds the front in relay mode, it
downloads the front's data (database, keys.env, encryption and push keys,
uploads, outputs, projects) through /__relay/export, installs it, restarts
its Stellar onto it, and only then says it is ready. RELAY_IMPORT=0 skips
this (the runner keeps its own data).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import io
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".relay"
LOG = ROOT / "relay.log"
CHUNK = 64 * 1024
MAX_BODY = 256 * 1024 * 1024          # uploads; main Stellar buffers bodies whole anyway
SKEW = 300                            # seconds a signature stays valid
STREAM_TIMEOUT = 3600                 # a reply may stream this long (Gunicorn's own limit)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailer", "trailers", "transfer-encoding", "upgrade", "content-length"}
RUNNER_URL = re.compile(r"^https://[a-z0-9-]+\.trycloudflare\.com$")
VERSION = "1"


# --------------------------------------------------------------- shared
def log(text: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + text
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def keys_env() -> dict[str, str]:
    """keys.env as a dict (read fresh each time: it can be edited live)."""
    found = {}
    path = ROOT / "keys.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                found[k.strip()] = v.strip().strip("'\"")
    return found


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name) or keys_env().get(name) or default


def secret() -> bytes:
    return setting("RELAY_SECRET").encode()


def sign(*parts) -> str:
    return hmac.new(secret(), "|".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def fresh(ts) -> bool:
    try:
        return abs(time.time() - int(ts)) <= SKEW
    except (TypeError, ValueError):
        return False


def ws_token() -> str:
    ts = int(time.time())
    return f"{ts}.{sign('ws', ts)}"


def ws_token_ok(token: str) -> bool:
    ts, _, mac = (token or "").partition(".")
    return bool(secret()) and fresh(ts) and hmac.compare_digest(mac, sign("ws", ts))


def clean_headers(pairs) -> list[list[str]]:
    return [[k, v] for k, v in pairs if k.lower() not in HOP]


# ---------------------------------------------------------------- front
class Runner:
    """Where the runner is, whether it is ready, and a pool of open
    WebSocket connections to it."""

    IDLE = 25        # an idle connection older than this is dropped (Cloudflare cuts ~100 s)
    STALE = 120      # no announce for this long: the runner is away

    def __init__(self):
        self.lock = threading.Lock()
        self.url = ""
        self.ready = False
        self.seen = 0.0
        self.failures = 0
        self.pool: list[tuple[object, float]] = []
        try:
            saved = json.loads((STATE / "runner.json").read_text())
            self.url, self.ready = saved.get("url", ""), bool(saved.get("ready"))
        except (OSError, ValueError):
            pass

    def announce(self, url: str, ready: bool) -> None:
        with self.lock:
            if url != self.url:
                self._drain()
                log(f"Runner announced at {url}")
            if ready and not self.ready:
                log("Runner is ready: serving from it")
            self.url, self.ready, self.seen, self.failures = url, ready, time.time(), 0
        try:
            STATE.mkdir(exist_ok=True)
            (STATE / "runner.json").write_text(json.dumps({"url": url, "ready": ready}))
        except OSError:
            pass

    def up(self) -> bool:
        return bool(self.url and self.ready and time.time() - self.seen < self.STALE
                    and self.failures < 3)

    def state(self) -> str:
        if not self.url:
            return "waiting"
        if self.up():
            return "up"
        return "moving in" if not self.ready and time.time() - self.seen < self.STALE else "down"

    def _drain(self) -> None:
        for ws, _ in self.pool:
            try:
                ws.close()
            except Exception:
                pass
        self.pool.clear()

    def take(self, fresh_only: bool = False):
        from websockets.sync.client import connect
        if not fresh_only:
            with self.lock:
                while self.pool:
                    ws, t = self.pool.pop()
                    if time.time() - t < self.IDLE:
                        return ws
                    try:
                        ws.close()
                    except Exception:
                        pass
        url = self.url.replace("https://", "wss://", 1) + "/relay"
        opts = dict(additional_headers={"X-Relay-Auth": ws_token()}, open_timeout=15,
                    max_size=None, compression=None)
        try:
            try:
                ws = connect(url, ping_interval=20, ping_timeout=30, **opts)
            except TypeError:                      # an older websockets without keepalive
                ws = connect(url, **opts)
        except Exception:
            with self.lock:
                self.failures += 1
            raise
        with self.lock:
            self.failures = 0
        return ws

    def give(self, ws) -> None:
        with self.lock:
            if len(self.pool) < 16:
                self.pool.append((ws, time.time()))
                return
        ws.close()


RUNNER = Runner()

OFFLINE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Stellar is offline</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05060a;
color:#e8e6f0;font:16px/1.6 system-ui,sans-serif}main{max-width:460px;padding:32px;text-align:center}
h1{font-weight:500;font-size:26px;letter-spacing:.02em;margin:0 0 12px}p{color:#9a98a8;margin:0 0 8px}
a{color:#b9a8ff}</style></head><body><main><h1>Stellar's workspace is offline</h1>
<p>It runs on its own machine, which is switched off or restarting right now.</p>
<p>This page checks again every 30 seconds. <a href="/welcome">About Stellar</a></p>
</main></body></html>"""

LANDING = ("/", "/welcome", "/cosmos", "/healthz", "/favicon.svg", "/favicon.ico", "/robots.txt",
           "/manifest.webmanifest", "/sw.js")


class Front(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StellarRelay/" + VERSION

    def log_message(self, fmt, *args):      # one line per request is too much
        pass

    # every method goes the same way
    def do_GET(self): self.handle_any()
    def do_HEAD(self): self.handle_any()
    def do_POST(self): self.handle_any()
    def do_PUT(self): self.handle_any()
    def do_PATCH(self): self.handle_any()
    def do_DELETE(self): self.handle_any()
    def do_OPTIONS(self): self.handle_any()

    def read_body(self) -> bytes:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if not size:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(out)
                out += self.rfile.read(size)
                self.rfile.readline()
                if len(out) > MAX_BODY:
                    raise ValueError("body too large")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def handle_any(self) -> None:
        try:
            body = self.read_body()
        except ValueError:
            self.simple(413, "Too large\n")
            return
        if self.path.startswith("/__relay/"):
            self.control(body)
            return
        if RUNNER.up():
            try:
                if self.via_runner(body):
                    return
            except Exception as exc:
                log(f"Runner failed on {self.command} {urlsplit(self.path).path}: "
                    f"{type(exc).__name__}: {exc}")
                if getattr(self, "_started", False):
                    self.close_connection = True
                    return
        self.fallback(body)

    # ---- to the runner
    def via_runner(self, body: bytes) -> bool:
        """Forward to the runner. False if it could not be reached before
        anything was sent back (so the fallback can still answer)."""
        self._started = False
        head = json.dumps({"m": self.command, "p": self.path, "h": clean_headers(self.headers.items())})
        ws = None
        for attempt in range(2):
            sent = False
            try:
                ws = RUNNER.take(fresh_only=attempt > 0)
                ws.send(head)
                for i in range(0, len(body), CHUNK):
                    ws.send(body[i:i + CHUNK])
                ws.send(b"")
                sent = True
                first = ws.recv(timeout=STREAM_TIMEOUT)
                break
            except Exception:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                ws = None
                # A connection that died while sending (a stale one from the
                # pool) is tried once more on a new one. Once the request has
                # gone through, it is never sent twice: a form must not
                # submit twice because a reply was lost.
                if attempt or sent:
                    return False
        reply = json.loads(first)
        if "err" in reply:
            log(f"Runner could not answer {self.command} {urlsplit(self.path).path}: {reply['err']}")
            ws.close()
            return False
        self.reply_head(reply["s"], reply.get("r", ""), reply["h"])
        try:
            while True:
                m = ws.recv(timeout=STREAM_TIMEOUT)
                if isinstance(m, (bytes, bytearray)):
                    self.reply_chunk(m)
                    continue
                if json.loads(m).get("end"):
                    break
                raise RuntimeError("runner reported an error mid-reply")
            self.reply_end()
        except (BrokenPipeError, ConnectionResetError):
            # the visitor went away: closing the WebSocket stops the runner's request
            ws.close()
            self.close_connection = True
            return True
        except Exception:
            ws.close()
            raise
        RUNNER.give(ws)
        return True

    # ---- when the runner is away
    def fallback(self, body: bytes) -> None:
        path = urlsplit(self.path).path
        full = setting("RELAY_FALLBACK", "landing") == "full"
        if full or path in LANDING or path.startswith("/static/"):
            headers = [[k, v] for k, v in clean_headers(self.headers.items())
                       if full or k.lower() != "cookie"]
            target = self.path
            if path == "/" and not full:
                target = "/welcome" + (("?" + urlsplit(self.path).query) if urlsplit(self.path).query else "")
            try:
                self.pipe_local(target, headers, body)
                return
            except OSError:
                pass
        if path.startswith("/api/") or "json" in (self.headers.get("Accept") or ""):
            self.simple(503, json.dumps({"error": "Stellar's workspace is offline. Try again soon."}),
                        "application/json", retry=True)
        else:
            self.simple(503, OFFLINE, "text/html; charset=utf-8", retry=True)

    def pipe_local(self, target: str, headers, body: bytes) -> None:
        port = int(setting("RELAY_LOCAL_PORT", "5002"))
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=STREAM_TIMEOUT)
        self._started = False
        try:
            conn.putrequest(self.command, target, skip_host=True, skip_accept_encoding=True)
            for k, v in headers:
                conn.putheader(k, v)
            if body or self.command in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body or None)
            resp = conn.getresponse()
            self.reply_head(resp.status, resp.reason, resp.getheaders())
            while True:
                piece = resp.read1(CHUNK) if self.command != "HEAD" else b""
                if not piece:
                    break
                self.reply_chunk(piece)
            self.reply_end()
        finally:
            conn.close()

    # ---- writing a reply
    def reply_head(self, status: int, reason: str, headers) -> None:
        self._started = True
        self._chunked = self.command != "HEAD" and status not in (204, 304) and status >= 200
        self.send_response_only(status, reason or None)
        for k, v in clean_headers(headers):
            self.send_header(k, v)
        if self._chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", "0")
        self.end_headers()
        self.wfile.flush()

    def reply_chunk(self, data: bytes) -> None:
        if data and self._chunked:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    def reply_end(self) -> None:
        if self._chunked:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def simple(self, status: int, text: str, ctype: str = "text/plain; charset=utf-8",
               retry: bool = False) -> None:
        data = text.encode()
        self.send_response_only(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if retry:
            self.send_header("Retry-After", "30")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ---- the runner's calls
    def control(self, body: bytes) -> None:
        path = urlsplit(self.path).path
        if path == "/__relay/status":
            age = int(time.time() - RUNNER.seen) if RUNNER.seen else None
            self.simple(200, json.dumps({"relay": True, "version": VERSION, "runner": RUNNER.state(),
                                         "last_seen_s": age}), "application/json")
            return
        if path == "/__relay/announce" and self.command == "POST":
            try:
                msg = json.loads(body)
                url, ready, ts = str(msg["url"]).rstrip("/"), bool(msg.get("ready")), msg["ts"]
                ok = (secret() and fresh(ts) and RUNNER_URL.match(url)
                      and hmac.compare_digest(str(msg.get("sig", "")), sign("announce", url, int(ready), ts)))
            except (ValueError, KeyError, TypeError):
                ok = False
            if not ok:
                self.simple(403, json.dumps({"error": "bad signature"}), "application/json")
                return
            RUNNER.announce(url, ready)
            self.simple(200, json.dumps({"relay": True, "version": VERSION}), "application/json")
            return
        if path == "/__relay/export" and self.command == "GET":
            q = parse_qs(urlsplit(self.path).query)
            ts, mac = (q.get("ts") or [""])[0], (q.get("sig") or [""])[0]
            if not (secret() and fresh(ts) and hmac.compare_digest(mac, sign("export", ts))):
                self.simple(403, json.dumps({"error": "bad signature"}), "application/json")
                return
            self.export()
            return
        self.simple(404, "Not found\n")

    def export(self) -> None:
        """This copy's data, for the runner to move in with."""
        tmp = Path(tempfile.mkdtemp(prefix="relay-export-"))
        try:
            bundle = tmp / "bundle.tar.gz"
            with tarfile.open(bundle, "w:gz") as tar:
                for name, path in data_paths(ROOT).items():
                    if name == "db":
                        if path.exists():
                            copy = tmp / "db.sqlite"
                            src = sqlite3.connect(str(path), timeout=30)
                            dst = sqlite3.connect(copy)
                            with dst:
                                src.backup(dst)     # consistent even while the app writes
                            src.close()
                            dst.close()
                            tar.add(copy, arcname="db")
                    elif path.exists():
                        tar.add(path, arcname=name)
            size = bundle.stat().st_size
            log(f"Export: sent this copy's data to the runner ({size // 1024} KB)")
            self.send_response_only(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with bundle.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, CHUNK)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def data_paths(root: Path) -> dict[str, Path]:
    """Where a copy of Stellar keeps what is its own (see app.py's
    _setting: keys.env or the environment can move each of these)."""
    env = {**keys_env(), **os.environ}

    def place(key: str, default: str) -> Path:
        p = Path(env.get(key) or default)
        return p if p.is_absolute() else root / p

    db = place("DATABASE_NAME", "stellar_local.db")
    return {
        "db": db,
        "keys.env": root / "keys.env",
        "encryption.key": db.with_name("encryption.key"),
        "vapid_private.pem": db.with_name("vapid_private.pem"),
        "uploads": place("STELLAR_UPLOADS_DIR", "uploads"),
        "outputs": place("STELLAR_OUTPUTS_DIR", "outputs"),
        "deployments": place("STELLAR_DEPLOYMENTS_DIR", "deployments"),
        "sandbox_runs": place("STELLAR_SANDBOX_DIR", "sandbox_runs"),
    }


def run_front() -> None:
    port = int(setting("RELAY_FRONT_PORT", "5000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Front)
    server.daemon_threads = True
    log(f"Relay front listening on :{port} (runner: {RUNNER.state()})")

    def stop(*_):
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except (SystemExit, KeyboardInterrupt):
        log("Relay front stopping")


# --------------------------------------------------------------- runner
class Gateway:
    """The WebSocket end on the runner: each exchange becomes one request to
    this machine's own Stellar."""

    def __init__(self, port: int, app_port: int):
        self.port, self.app_port = port, app_port

    def check(self, connection, request):
        if not ws_token_ok(request.headers.get("X-Relay-Auth", "")):
            log("Gateway: refused a connection without a valid signature")
            return connection.respond(401, "Unauthorized\n")
        return None

    def handler(self, ws) -> None:
        from websockets.exceptions import ConnectionClosed
        try:
            while True:
                head = json.loads(ws.recv())
                body = bytearray()
                while True:
                    piece = ws.recv()
                    if not piece:
                        break
                    body += piece
                self.forward(ws, head, bytes(body))
        except ConnectionClosed:
            return

    def forward(self, ws, head: dict, body: bytes) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.app_port, timeout=STREAM_TIMEOUT)
        try:
            try:
                conn.putrequest(head["m"], head["p"], skip_host=True, skip_accept_encoding=True)
                for k, v in head["h"]:
                    conn.putheader(k, v)
                if body or head["m"] in ("POST", "PUT", "PATCH"):
                    conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body or None)
                resp = conn.getresponse()
            except OSError as exc:
                ws.send(json.dumps({"err": f"Stellar on the runner did not answer: {exc}"}))
                return
            ws.send(json.dumps({"s": resp.status, "r": resp.reason, "h": clean_headers(resp.getheaders())}))
            if head["m"] != "HEAD":
                while True:
                    piece = resp.read1(CHUNK)
                    if not piece:
                        break
                    ws.send(piece)
            ws.send(json.dumps({"end": True}))
        finally:
            conn.close()

    def serve(self) -> None:
        from websockets.sync.server import serve
        with serve(self.handler, "127.0.0.1", self.port, process_request=self.check,
                   max_size=None, compression=None, ping_interval=20, ping_timeout=30) as server:
            log(f"Gateway listening on 127.0.0.1:{self.port}, forwarding to 127.0.0.1:{self.app_port}")
            server.serve_forever()


class Tunnel:
    """A Cloudflare quick tunnel to the gateway; restarted if it stops."""

    def __init__(self, port: int):
        self.port = port
        self.url = ""
        self.proc = None

    def run(self) -> None:
        delay = 2
        while True:
            started = time.time()
            self.proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in self.proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and m.group(0) != self.url:
                    self.url = m.group(0)
                    log(f"Tunnel up: {self.url}")
            self.proc.wait()
            self.url = ""
            delay = 2 if time.time() - started > 120 else min(delay * 2, 60)
            log(f"Tunnel stopped (exit {self.proc.returncode}); again in {delay}s")
            time.sleep(delay)


class AppTunnels:
    """A quick tunnel for each deployed app running here, its address kept
    in Redis (app_tunnel:<process_id>), where Stellar's /a/<name>/ link
    finds it. An app's own address, not a subdomain of Stellar's, also
    keeps it away from Stellar's cookies entirely."""

    EVERY = 10

    def __init__(self):
        self.live: dict[str, dict] = {}      # process_id -> {"port", "proc", "url"}

    def run(self) -> None:
        said = ""
        while True:
            try:
                self.sync()
                said = ""
            except Exception as exc:
                text = f"App tunnels: {type(exc).__name__}: {exc}"
                if text != said:
                    log(text)
                    said = text
            time.sleep(self.EVERY)

    def sync(self) -> None:
        import docker
        import redis
        client = docker.from_env(timeout=10)
        r = redis.Redis.from_url(setting("REDIS_URL"), decode_responses=True, socket_timeout=5)
        prefix = setting("STELLAR_CONTAINER_PREFIX", "stellar") + "-repo-"
        running = {}
        for c in client.containers.list(filters={"label": "stellar=repo", "status": "running"}):
            pid = (c.labels or {}).get("process_id")
            if not pid or not (c.name or "").startswith(prefix):
                continue
            for binds in ((c.attrs.get("NetworkSettings") or {}).get("Ports") or {}).values():
                for b in binds or []:
                    if b.get("HostIp") == "127.0.0.1" and b.get("HostPort"):
                        running[pid] = int(b["HostPort"])
        for pid in list(self.live):
            t = self.live[pid]
            if running.get(pid) != t["port"] or t["proc"].poll() is not None:
                t["proc"].terminate()
                r.delete(f"app_tunnel:{pid}")
                del self.live[pid]
                if pid not in running:
                    log(f"App tunnel closed: {pid} (no longer running)")
        for pid, port in running.items():
            if pid not in self.live:
                self.live[pid] = self.open(pid, port)
        for pid, t in self.live.items():
            if t["url"]:
                r.set(f"app_tunnel:{pid}", t["url"], ex=self.EVERY * 9)

    def open(self, pid: str, port: int) -> dict:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
             "--http-host-header", f"127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        t = {"port": port, "proc": proc, "url": ""}

        def read():
            for line in proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not t["url"]:
                    t["url"] = m.group(0)
                    log(f"App tunnel open: {pid} -> {t['url']}")

        threading.Thread(target=read, daemon=True).start()
        return t

    def close_all(self) -> None:
        for t in self.live.values():
            t["proc"].terminate()


class Mover:
    """Brings the front's data over once, the first time it is in relay mode."""

    MARK = STATE / "imported"

    def needed(self) -> bool:
        return setting("RELAY_IMPORT", "1") != "0" and not self.MARK.exists()

    def run(self, front: str) -> bool:
        ts = int(time.time())
        url = f"{front}/__relay/export?ts={ts}&sig={sign('export', ts)}"
        tmp = Path(tempfile.mkdtemp(prefix="relay-import-"))
        try:
            log("Moving in: downloading the front's data")
            req = urllib.request.Request(url, headers={"User-Agent": "StellarRelay/" + VERSION})
            with urllib.request.urlopen(req, timeout=600) as r, (tmp / "b.tgz").open("wb") as f:
                shutil.copyfileobj(r, f, CHUNK)
            with tarfile.open(tmp / "b.tgz") as tar:
                tar.extractall(tmp / "x", filter="data")
            self.install(tmp / "x")
            STATE.mkdir(exist_ok=True)
            self.MARK.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
            log("Moved in: the front's data is installed here")
            return True
        except Exception as exc:
            log(f"Moving in failed ({type(exc).__name__}: {exc}); will try again")
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def install(self, src: Path) -> None:
        if (src / "db").exists():
            with sqlite3.connect((src / 'db').resolve().as_uri() + "?mode=ro", uri=True) as imported:
                if imported.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("Imported database failed its integrity check")
        keep = ROOT / ".relay" / ("before-import-" + time.strftime("%Y%m%d-%H%M%S"))
        keep.mkdir(parents=True, exist_ok=True)
        # Stop writers before replacing configuration or the database.
        # Redis belongs to the runner and its configuration is retained.
        stop_local_stellar()
        previous_db = data_paths(ROOT)["db"]
        if previous_db.exists():
            with sqlite3.connect(str(previous_db), timeout=5) as previous:
                if previous.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise RuntimeError("Database still has active writers; import cancelled")
        # keys.env first: it says where everything else goes. Paths the front
        # gave as /app/... are this checkout here.
        mine = keys_env()
        if (src / "keys.env").exists():
            text = (src / "keys.env").read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"(=\s*['\"]?)/app/", lambda m: m.group(1) + str(ROOT) + "/", text)
            # What belongs to this machine stays this machine's: its Redis
            # (another port here) and the relay's own settings.
            own = {k: v for k, v in mine.items() if k == "REDIS_URL" or k.startswith("RELAY_")}
            lines = [ln for ln in text.splitlines() if ln.split("=", 1)[0].strip() not in own]
            text = "\n".join(lines).rstrip("\n") + "\n\n# this machine's own (kept when moving in)\n"
            text += "".join(f"{k}={v}\n" for k, v in own.items())
            if (ROOT / "keys.env").exists():
                shutil.copy2(ROOT / "keys.env", keep / "keys.env")
            (ROOT / "keys.env").write_text(text, encoding="utf-8")
        here = data_paths(ROOT)
        for name, dest in here.items():
            if name == "keys.env":
                continue  # already merged above; never overwrite runner Redis/relay settings
            item = src / name
            if not item.exists():
                continue
            if dest.exists():
                shutil.move(str(dest), str(keep / name))
            if name == "db":
                for side in ("-wal", "-shm"):
                    Path(str(dest) + side).unlink(missing_ok=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(dest))
        log(f"Previous data kept in {keep.relative_to(ROOT)}")


def stop_local_stellar() -> None:
    """Stop only this checkout's app writers, retaining the runner's Redis."""
    stopped = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            args = [arg.decode(errors="replace") for arg in
                    Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if arg]
        except OSError:
            continue
        executable = any(Path(arg).name in ("gunicorn", "gunicorn.exe") for arg in args[:2])
        module = len(args) > 2 and args[1:3] == ["-m", "gunicorn"]
        title = bool(args and args[0].startswith("gunicorn: ") and "[app:create_app" in args[0])
        application = any(arg in ("app:create_app()", "app:create_app") for arg in args)
        if int(pid) != os.getpid() and (title or ((executable or module) and application)):
            try:
                if Path(f"/proc/{pid}/cwd").resolve() != ROOT.resolve():
                    continue
                os.kill(int(pid), signal.SIGTERM)
                stopped.append(pid)
            except OSError:
                pass
    deadline = time.monotonic() + 30
    while stopped:
        alive = []
        for pid in stopped:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                if state != "Z":
                    alive.append(pid)
            except FileNotFoundError:
                pass
        if not alive:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Stellar writers did not stop; data import cancelled")
        stopped = alive
        time.sleep(0.1)


def app_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            s.sendall(b"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            return s.recv(12).startswith(b"HTTP/1.")
    except OSError:
        return False


def run_runner() -> None:
    if not secret():
        sys.exit("RELAY_SECRET is not set in keys.env")
    gw_port = int(setting("RELAY_GATEWAY_PORT", "8089"))
    app_port = int(setting("RELAY_APP_PORT", "5000"))
    front = setting("RELAY_FRONT", "https://new-stellar.stellarai.site").rstrip("/")
    gateway, tunnel, mover, apps = Gateway(gw_port, app_port), Tunnel(gw_port), Mover(), AppTunnels()
    threading.Thread(target=gateway.serve, daemon=True).start()
    threading.Thread(target=tunnel.run, daemon=True).start()
    threading.Thread(target=apps.run, daemon=True).start()
    me = Path(__file__).resolve()
    version = me.read_bytes()
    said = ""
    log(f"Runner started; front is {front}")

    def stop(*_):
        apps.close_all()
        if tunnel.proc:
            tunnel.proc.terminate()
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    while True:
        time.sleep(1 if not tunnel.url else 0)
        if me.read_bytes() != version:
            log("relay.py changed (pulled): restarting into the new version")
            apps.close_all()
            if tunnel.proc:
                tunnel.proc.terminate()
            os.execv(sys.executable, [sys.executable, str(me), *sys.argv[1:]])
        if not tunnel.url:
            continue
        ready = app_answers(app_port) and not mover.needed()
        ts = int(time.time())
        body = json.dumps({"url": tunnel.url, "ready": ready, "ts": ts,
                           "sig": sign("announce", tunnel.url, int(ready), ts)}).encode()
        try:
            req = urllib.request.Request(front + "/__relay/announce", data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "StellarRelay/" + VERSION})
            with urllib.request.urlopen(req, timeout=20) as r:
                reply = json.loads(r.read() or b"{}")
            state = "front in relay mode" + ("; serving" if ready else "; not ready yet")
            if reply.get("relay") and mover.needed() and app_answers(app_port):
                if mover.run(front):
                    continue            # announce again at once, now ready (after the restart)
        except urllib.error.HTTPError as exc:
            text = exc.read(2000).decode(errors="replace")
            if "bad signature" in text:
                state = "front refused the signature: RELAY_SECRET differs between the two keys.env"
            else:
                # the plain app answers (404, or a CSRF refusal): no relay there yet
                state = (f"front not in relay mode yet (HTTP {exc.code}): add RELAY_SECRET to the "
                         "container's keys.env")
        except (OSError, ValueError) as exc:
            state = f"front unreachable ({type(exc).__name__})"
        if state != said:
            log(state)
            said = state
        time.sleep(30)


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "front":
        run_front()
    elif role == "runner":
        run_runner()
    else:
        sys.exit(__doc__)
