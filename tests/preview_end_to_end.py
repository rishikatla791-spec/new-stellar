"""Combined HTTPS launch: Flask -> real Redis -> gateway -> owned Docker app."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
import tempfile
import threading
import time

import docker
import redis.asyncio as redis_async
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A
from deploy.preview.gateway import Gateway
from browser_integration import SERVER

SCRIPT = r'''
import json,sys
from playwright.sync_api import sync_playwright,expect
spec=json.loads(sys.argv[1])
with sync_playwright() as p:
    browser=p.chromium.launch()
    context=browser.new_context(ignore_https_errors=True,viewport={'width':1600,'height':1000})
    context.add_cookies([{'name':'__Host-stellar_session','value':spec['cookie'],'url':spec['url'],'secure':True,'httpOnly':True}])
    page=context.new_page()
    page.goto(spec['url'],wait_until='domcontentloaded')
    page.locator('#nav-preview').click()
    expect(page.locator('#preview-project option[value="'+spec['project']+'"]')).to_have_count(1)
    page.locator('#preview-project').select_option(spec['project'])
    frame=page.frame_locator('#preview-viewport iframe')
    expect(frame.locator('h1')).to_have_text('Browser fixture')
    frame.locator('#name').fill('Real end-to-end preview')
    frame.locator('#go').click()
    expect(frame.locator('#result')).to_have_text('Real end-to-end preview')
    page.wait_for_function('state.chatId === 1')
    page.locator('#preview-test').click()
    expect(page.locator('#preview-evidence')).to_contain_text('Browser check passed',timeout=90000)
    report_url=page.locator('#preview-evidence a').first.get_attribute('href')
    report=page.request.get(spec['url'].rstrip('/')+report_url).json()
    assert report['passed'] is True and report['screenshot'],report
    cookies=context.cookies()
    preview_cookie=next(c for c in cookies if c['name']=='__Host-stellar_preview')
    assert preview_cookie['httpOnly'] and preview_cookie['secure'] and not preview_cookie['domain'].startswith('.')
    browser.close()
    print(json.dumps({'passed':True}))
'''


def certificate(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "stellar-preview-fixture")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("stellar.localhost"),
            x509.DNSName("*.preview.localhost")]), critical=False).sign(key, hashes.SHA256()))
    certfile, keyfile = folder / "cert.pem", folder / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return str(certfile), str(keyfile)


async def main():
    client = docker.from_env(timeout=120)
    prefix = "stellar-e2e-" + secrets.token_hex(4)
    project = secrets.token_hex(6)
    target = runner = main_server = gateway_server = redis = None
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        try:
            target = await asyncio.to_thread(client.containers.run, "stellar-browser:1.63.0",
                command=["python", "-u", "-c", SERVER], name=f"{prefix}-repo-{project}",
                detach=True, ports={"5000/tcp": ("127.0.0.1", 0)},
                labels={"stellar": "repo", "user": "1", "process_id": project})
            for _ in range(30):
                probe = await asyncio.to_thread(target.exec_run, ["python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/')"])
                if probe.exit_code == 0: break
                await asyncio.sleep(.2)
            else: raise AssertionError("Fixture did not start")
            certfiles = certificate(folder)
            app = A.create_app({"TESTING": True, "BACKGROUND_THREADS": False,
                "SECRET_KEY": "preview-combined-fixture-only", "SESSION_COOKIE_NAME": "__Host-stellar_session",
                "SESSION_COOKIE_SECURE": True, "CSRF_TOKENS": False,
                "DATABASE": str(folder / "e2e.db"), "OUTPUTS_DIR": str(folder / "outputs"),
                "SANDBOX_DIR": str(folder / "labs"), "DEPLOYMENTS_DIR": str(folder / "apps"),
                "CONTAINER_PREFIX": prefix, "BROWSER_CHECKS_ENABLED": True})
            with app.app_context():
                A.init_db()
                db = A.get_db()
                db.execute("INSERT INTO users(id,username,password_hash,is_approved) VALUES(1,'e2e@example.com','',1)")
                db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Combined preview')")
                db.execute("INSERT INTO repo_history(user_id,project_name,process_id,subdomain,status) VALUES(1,'Combined preview',?,'fixture','running')", (project,))
                db.commit()
            main_server = make_server("127.0.0.1", 0, app, threaded=True, ssl_context=certfiles)
            parent = f"https://stellar.localhost:{main_server.server_port}"
            # Bind the gateway before choosing its public origin, then update
            # the validated config once the ephemeral port is known.
            app.config.update(STELLAR_APP_ORIGIN=parent, STELLAR_PREVIEW_ORIGIN="https://preview.localhost:1")
            redis = redis_async.Redis.from_url(app.config["REDIS_URL"], socket_timeout=3)
            gateway = Gateway(dict(app.config), redis, client)
            gateway_server = TestServer(gateway.application(), access_log=None)
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(*certfiles)
            await gateway_server.start_server(ssl=ssl_context)
            app.config["STELLAR_PREVIEW_ORIGIN"] = f"https://preview.localhost:{gateway_server.port}"
            from preview_access import PreviewConfig
            gateway.config = PreviewConfig.from_mapping(app.config)
            thread = threading.Thread(target=main_server.serve_forever, daemon=True)
            thread.start()
            flask_client = app.test_client()
            with flask_client.session_transaction() as session:
                session["user_id"] = 1
                session["epoch"] = 0
            cookie = flask_client.get_cookie("__Host-stellar_session").value
            runner = await asyncio.to_thread(client.containers.run, "stellar-browser:1.63.0",
                command=["python", "-c", SCRIPT, json.dumps({"url": parent + "/", "cookie": cookie, "project": project})],
                network_mode="host", detach=True, labels={"stellar": "preview-e2e-fixture"})
            status = await asyncio.to_thread(runner.wait, timeout=120)
            if status["StatusCode"] != 0:
                logs = await asyncio.to_thread(runner.logs, stdout=False, stderr=True)
                raise AssertionError(logs.decode()[-6000:])
            print("PASS real HTTPS iframe, interaction, owned browser report and secure cookie exchange")
        finally:
            if runner is not None: await asyncio.to_thread(runner.remove, force=True)
            if gateway_server is not None: await gateway_server.close()
            if redis is not None: await redis.aclose()
            if main_server is not None:
                await asyncio.to_thread(main_server.shutdown)
                main_server.server_close()
            if target is not None: await asyncio.to_thread(target.remove, force=True)


if __name__ == "__main__":
    asyncio.run(main())
