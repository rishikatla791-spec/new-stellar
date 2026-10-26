"""Optional HTTP/WebSocket preview gateway. Bind only to loopback behind TLS.

Run from the repository root: python -m deploy.preview.gateway
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web, WSMsgType
import docker
from dotenv import load_dotenv
import redis.asyncio as redis_async

from app import PreviewConfig, SESSION_TTL, TOKEN

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
TOTAL = 32
PER_PROJECT = 8
MAX_BODY = 8 * 1024 * 1024
ACCESS_POLL_SECONDS = 10


def forwarded_headers(headers, cookie_name):
    connection = {v.strip().lower() for v in headers.get("Connection", "").split(",")}
    excluded = HOP | connection | {"cookie", "forwarded"}
    result = {k: v for k, v in headers.items()
              if k.lower() not in excluded and not k.lower().startswith("x-forwarded-")}
    from http.cookies import SimpleCookie
    cookies = SimpleCookie()
    try:
        cookies.load(headers.get("Cookie", ""))
    except Exception:
        cookies.clear()
    reserved = {cookie_name, "stellar_session_main", "__Host-stellar_session"}
    clean = [m.OutputString() for name, m in cookies.items() if name not in reserved]
    if clean:
        result["Cookie"] = "; ".join(clean)
    return result


def target_for(config, payload, docker_client):
    """Revalidate durable ownership and the live container on every request."""
    with sqlite3.connect(Path(config["DATABASE"]).resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        row = db.execute(
            "SELECT u.is_approved, u.session_epoch, r.status FROM users u "
            "JOIN repo_history r ON r.user_id=u.id "
            "WHERE u.id=? AND r.process_id=?",
            (payload["user_id"], payload["project_id"])).fetchone()
    if not row or not row[0] or row[1] != payload["epoch"]:
        raise web.HTTPForbidden(text="Preview access has ended. Open it again from Stellar.")
    if row[2] != "running":
        raise web.HTTPConflict(text="This project is stopped. Wake it from Deploy in Stellar.")
    container = docker_client.containers.get(
        f"{config.get('CONTAINER_PREFIX', 'stellar')}-repo-{payload['project_id']}")
    labels = container.labels or {}
    if (container.status != "running" or labels.get("stellar") != "repo"
            or labels.get("process_id") != payload["project_id"]
            or labels.get("user") != str(payload["user_id"])):
        raise web.HTTPServiceUnavailable(text="The project container is unavailable.")
    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    binds = [b for values in ports.values() for b in (values or [])
             if b.get("HostIp") == "127.0.0.1" and str(b.get("HostPort", "")).isdigit()]
    if len(binds) != 1 or not 1 <= int(binds[0]["HostPort"]) <= 65535:
        raise web.HTTPServiceUnavailable(text="The project has no unique preview port.")
    return "http://127.0.0.1:" + str(int(binds[0]["HostPort"]))


class Gateway:
    def __init__(self, config, redis, docker_client):
        self.settings = config
        self.config = PreviewConfig.from_mapping(config)
        self.redis = redis
        self.docker = docker_client
        self.active = 0
        self.authenticating = 0
        self.projects = {}
        self.http = None

    async def authorize(self, request):
        if request.path == "/__stellar_preview/launch":
            token = request.query.get("ticket", "")
            # redis.asyncio has the same atomic GETDEL semantics as the
            # synchronous issuer, without blocking this service's event loop.
            if not TOKEN.fullmatch(token):
                raise web.HTTPUnauthorized(text="Invalid preview ticket.")
            value = await self.redis.getdel(self.config.key("ticket", token))
            payload = json.loads(value) if value else None
        else:
            token = request.cookies.get(self.config.cookie_name, "")
            value = await self.redis.get(self.config.key("session", token)) if TOKEN.fullmatch(token) else None
            payload = json.loads(value) if value else None
        if not payload:
            raise web.HTTPUnauthorized(text="Preview access expired. Reload the preview in Stellar.")
        expected = urlsplit(self.config.project_origin(payload["project_id"])).netloc
        if request.host.lower() != expected:
            raise web.HTTPForbidden(text="This preview belongs to a different project.")
        target = await asyncio.to_thread(target_for, self.settings, payload, self.docker)
        return payload, target

    def protect(self, response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        policies = response.headers.getall("Content-Security-Policy", [])
        if policies:
            del response.headers["Content-Security-Policy"]
        for policy in policies or [""]:
            directives = [part.strip() for part in policy.split(";")
                          if part.strip() and part.strip().split()[0].lower() != "frame-ancestors"]
            directives.append(f"frame-ancestors {self.config.parent}")
            response.headers.add("Content-Security-Policy", "; ".join(directives))
        return response

    async def handle(self, request):
        try:
            if self.authenticating >= TOTAL:
                raise web.HTTPTooManyRequests(text="Preview is busy. Try again shortly.")
            self.authenticating += 1
            try:
                payload, target = await self.authorize(request)
            finally:
                self.authenticating -= 1
            if request.path == "/__stellar_preview/launch":
                if request.method != "GET":
                    raise web.HTTPMethodNotAllowed(request.method, ["GET"])
                token = secrets.token_urlsafe(32)
                await self.redis.setex(self.config.key("session", token), SESSION_TTL, json.dumps(payload))
                response = web.Response(status=302, headers={"Location": "/"})
                response.set_cookie(self.config.cookie_name, token, max_age=SESSION_TTL,
                    secure=self.config.base.startswith("https:"), httponly=True,
                    samesite="None" if self.config.base.startswith("https:") else "Lax", path="/")
                return self.protect(response)
            expected_origin = self.config.project_origin(payload["project_id"])
            if request.headers.get("Service-Worker", "").lower() == "script":
                raise web.HTTPForbidden(text="Service workers are disabled in live previews.")
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("Origin") != expected_origin:
                raise web.HTTPForbidden(text="Request origin does not match this preview.")
            pid = payload["project_id"]
            if self.active >= TOTAL or self.projects.get(pid, 0) >= PER_PROJECT:
                raise web.HTTPTooManyRequests(text="Preview is busy. Try again shortly.", headers={"Retry-After": "3"})
            self.active += 1
            self.projects[pid] = self.projects.get(pid, 0) + 1
            try:
                headers = forwarded_headers(request.headers, self.config.cookie_name)
                headers["X-Forwarded-Host"] = request.host
                headers["X-Forwarded-Proto"] = urlsplit(self.config.base).scheme
                if request.headers.get("Upgrade", "").lower() == "websocket":
                    return await self.websocket(request, target, headers)
                return await self.proxy(request, target, headers)
            finally:
                self.active -= 1
                self.projects[pid] -= 1
                if not self.projects[pid]:
                    self.projects.pop(pid, None)
        except web.HTTPException as exc:
            self.protect(exc)
            return web.Response(status=exc.status, body=exc.body, headers=exc.headers)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return self.protect(web.Response(status=502, text="The project server did not respond. Check its logs in Deploy."))
        except Exception:
            # No token, container details or upstream URL reaches the visitor.
            return self.protect(web.Response(status=503, text="Preview dependencies are unavailable. Try again later."))

    async def proxy(self, request, target, headers):
        if request.content_length and request.content_length > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
        # Buffer only the capped upload; response bodies stream without buffering.
        body = await request.read()
        async with self.http.request(request.method, target + request.raw_path,
                headers=headers, data=body, allow_redirects=False) as upstream:
            excluded = HOP | {"set-cookie", "x-frame-options", "clear-site-data"}
            connection = {v.strip().lower() for v in upstream.headers.get("Connection", "").split(",")}
            out = [(k, v) for k, v in upstream.headers.items() if k.lower() not in excluded | connection
                   and not k.lower().startswith("access-control-")]
            response = self.protect(web.StreamResponse(status=upstream.status, headers=out))
            from http.cookies import SimpleCookie
            for raw in upstream.headers.getall("Set-Cookie", []):
                cookies = SimpleCookie()
                try:
                    cookies.load(raw)
                except Exception:
                    continue
                if len(cookies) == 1:
                    name, cookie = next(iter(cookies.items()))
                    if not cookie["domain"] and name not in (self.config.cookie_name, "stellar_session_main", "__Host-stellar_session"):
                        response.headers.add("Set-Cookie", raw)
            location = response.headers.get("Location", "")
            if location == target or location.startswith(target + "/"):
                response.headers["Location"] = self.config.project_origin((await self.authorize(request))[0]["project_id"]) + location[len(target):]
            await response.prepare(request)
            checked = time.monotonic()
            try:
                async for chunk in upstream.content.iter_chunked(65536):
                    if time.monotonic() - checked >= ACCESS_POLL_SECONDS:
                        await self.authorize(request)
                        checked = time.monotonic()
                    await response.write(chunk)
                await response.write_eof()
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, web.HTTPException):
                request.transport.close() if request.transport else None
            return response

    async def websocket(self, request, target, headers):
        # Prevent another origin from exercising a user's preview session.
        expected_origin = self.config.project_origin((await self.authorize(request))[0]["project_id"])
        if request.headers.get("Origin") != expected_origin:
            raise web.HTTPForbidden(text="WebSocket origin does not match this preview.")
        protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        headers = {k: v for k, v in headers.items() if not k.lower().startswith("sec-websocket-")}
        async with self.http.ws_connect(target + request.raw_path, headers=headers,
                protocols=protocols, max_msg_size=1024 * 1024, heartbeat=25,
                timeout=aiohttp.ClientWSTimeout(ws_receive=120, ws_close=5)) as upstream:
            response = self.protect(web.WebSocketResponse(
                protocols=[upstream.protocol] if upstream.protocol else (),
                max_msg_size=1024 * 1024, heartbeat=25, receive_timeout=120))
            await response.prepare(request)

            async def relay(source, dest):
                async for message in source:
                    if message.type == WSMsgType.TEXT:
                        await dest.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await dest.send_bytes(message.data)
                    else:
                        break
            tasks = [asyncio.create_task(relay(response, upstream))]
            tasks.append(asyncio.create_task(relay(upstream, response)))
            async def watch_access():
                while True:
                    await asyncio.sleep(ACCESS_POLL_SECONDS)
                    await self.authorize(request)
            tasks.append(asyncio.create_task(watch_access()))
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await response.close()
            return response

    async def context(self, app):
        self.http = aiohttp.ClientSession(auto_decompress=False, trust_env=False,
            timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=60),
            connector=aiohttp.TCPConnector(limit=TOTAL, limit_per_host=PER_PROJECT))
        yield
        await self.http.close()

    def application(self):
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_route("*", "/{path:.*}", self.handle)
        app.cleanup_ctx.append(self.context)
        return app


def main():
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / "keys.env")
    config = dict(os.environ)
    config["DATABASE"] = str(root / os.getenv("DATABASE_NAME", "stellar_local.db"))
    config["CONTAINER_PREFIX"] = os.getenv("STELLAR_CONTAINER_PREFIX", "stellar")
    gateway = Gateway(config,
        redis_async.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=3),
        docker.from_env(timeout=5))
    web.run_app(gateway.application(), host="127.0.0.1",
        port=int(os.getenv("STELLAR_PREVIEW_PORT", "5051")), access_log=None)


if __name__ == "__main__":
    main()
