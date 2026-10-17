"""Real Chromium checks against the actual Stellar page and preview scripts.

Only the launch/test responses and remote iframe contents are controlled
fixtures. This test uses a disposable local DB and no production account.
"""
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading

import docker
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A

SCRIPT = r'''
import base64,json,sys
from playwright.sync_api import sync_playwright,expect
spec=json.loads(sys.argv[1])
with sync_playwright() as p:
    browser=p.chromium.launch()
    context=browser.new_context(viewport={'width':1600,'height':1000})
    context.add_cookies([{'name':'stellar_session_main','value':spec['cookie'],'url':spec['url']}])
    mode={'value':'ok'}; held=[]
    def launch(route):
        if mode['value']=='hold': held.append(route);return
        if mode['value']=='error': route.fulfill(status=503,json={'error':'Fixture gateway is offline'});return
        route.fulfill(json={'origin':'https://p-abcdef123456.preview.example.net',
            'launch_url':'https://p-abcdef123456.preview.example.net/__stellar_preview/launch?ticket=fixture'})
    context.route('**/api/projects/abcdef123456/preview',launch)
    context.route('https://p-abcdef123456.preview.example.net/**',lambda route:route.fulfill(
        content_type='text/html',body='<title>Live fixture</title><h1>Live application</h1><p>Connected preview fixture</p>'))
    context.route('**/api/projects/abcdef123456/browser-check',lambda route:route.fulfill(json={
        'passed':False,'report':'/api/outputs/1/browser-fixture.json','screenshot':'/api/outputs/1/browser-fixture.jpg'}))
    page=context.new_page(); errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(spec['url'],wait_until='domcontentloaded')
    page.locator('#nav-preview').click()
    expect(page.locator('#preview-project option[value="abcdef123456"]')).to_have_count(1)
    page.locator('#preview-project').select_option('abcdef123456')
    expect(page.frame_locator('#preview-viewport iframe').locator('h1')).to_have_text('Live application')
    expect(page.locator('#preview-viewport iframe')).to_have_attribute('sandbox','allow-scripts allow-same-origin allow-forms')
    screenshots={'desktop':base64.b64encode(page.screenshot()).decode()}
    with page.expect_popup() as opened:
        page.locator('#preview-external').click()
    popup=opened.value
    expect(popup.locator('h1')).to_have_text('Live application')
    assert popup.evaluate('window.opener === null')
    popup.close()
    page.locator('#preview-test').click()
    expect(page.locator('#preview-evidence')).to_be_visible()
    expect(page.locator('#preview-evidence')).to_contain_text('Browser check failed')
    mode['value']='error'
    page.locator('#preview-reload').click()
    expect(page.locator('#preview-status')).to_have_text('Fixture gateway is offline')
    mode['value']='hold'
    page.locator('#preview-reload').click()
    page.wait_for_timeout(100)
    page.locator('#preview-close').click()
    for route in held:
        route.fulfill(json={'origin':'https://p-abcdef123456.preview.example.net',
            'launch_url':'https://p-abcdef123456.preview.example.net/__stellar_preview/launch?ticket=fixture'})
    page.wait_for_timeout(100)
    expect(page.locator('#preview-pane')).to_be_hidden()
    expect(page.locator('#preview-viewport iframe')).to_have_count(0)
    mode['value']='ok'
    page.set_viewport_size({'width':390,'height':844})
    page.locator('#menu-btn').click()
    page.locator('#nav-preview').click()
    expect(page.locator('#preview-pane')).to_be_visible()
    expect(page.locator('#preview-project option[value="abcdef123456"]')).to_have_count(1)
    page.locator('#preview-project').select_option('abcdef123456')
    expect(page.frame_locator('#preview-viewport iframe').locator('h1')).to_have_text('Live application')
    box=page.locator('#preview-pane').bounding_box()
    assert box['width'] <= 390 and box['x'] >= 0,box
    screenshots['mobile']=base64.b64encode(page.screenshot()).decode()
    page.locator('#preview-close').click()
    expect(page.locator('#preview-pane')).to_be_hidden()
    assert not errors,errors
    browser.close()
    print(json.dumps({'passed':True,'screenshots':screenshots}))
'''


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        app = A.create_app({"TESTING": True, "BACKGROUND_THREADS": False,
            "SECRET_KEY": "preview-ui-fixture-only", "SESSION_COOKIE_NAME": "stellar_session_main",
            "SESSION_COOKIE_SECURE": False, "CSRF_TOKENS": False,
            "DATABASE": str(folder / "ui.db"), "OUTPUTS_DIR": str(folder / "outputs"),
            "SANDBOX_DIR": str(folder / "labs"), "DEPLOYMENTS_DIR": str(folder / "apps"),
            "STELLAR_APP_ORIGIN": "https://stellar.example.org",
            "STELLAR_PREVIEW_ORIGIN": "https://preview.example.net", "BROWSER_CHECKS_ENABLED": True})
        with app.app_context():
            A.init_db()
            db = A.get_db()
            db.execute("INSERT INTO users(id,username,password_hash,is_approved) VALUES(1,'fixture@example.com','',1)")
            db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Browser UI fixture')")
            db.execute("INSERT INTO repo_history(user_id,project_name,process_id,subdomain,status) VALUES(1,'Preview fixture','abcdef123456','fixture','running')")
            db.commit()
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["epoch"] = 0
        cookie = client.get_cookie("stellar_session_main").value
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        docker_client = docker.from_env(timeout=120)
        container = None
        try:
            # Only this trusted UI fixture uses host networking to reach the
            # ephemeral local Flask server. Production browser checks never do.
            container = docker_client.containers.run("stellar-browser:1.63.0",
                command=["python", "-c", SCRIPT, json.dumps({"url": f"http://127.0.0.1:{server.server_port}/", "cookie": cookie})],
                detach=True, network_mode="host", labels={"stellar": "preview-ui-fixture"})
            status = container.wait(timeout=120)
            if status["StatusCode"] != 0:
                raise AssertionError(container.logs(stderr=True, stdout=False).decode()[-5000:])
            report = json.loads(container.logs(stdout=True, stderr=False))
            out = root / ".test-tmp"
            out.mkdir(exist_ok=True)
            for name, encoded in report["screenshots"].items():
                path = out / f"preview-{name}.png"
                path.write_bytes(base64.b64decode(encoded))
                print("PASS", name, "UI:", path)
            print("PASS external tab, failed check evidence, gateway failure and stale launch cancellation")
        finally:
            if container is not None:
                container.remove(force=True)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
