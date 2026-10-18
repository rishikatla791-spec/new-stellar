"""Real desktop/mobile browser checks with isolated health and account fixtures."""
import base64
import json
import sys
import tempfile
import threading
from pathlib import Path
import docker
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A

SCRIPT = r'''
import base64,json,sys
from playwright.sync_api import sync_playwright,expect
spec=json.loads(sys.argv[1])
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1280,'height':900})
    context.add_cookies([{'name':'stellar_session_main','value':spec['cookie'],'url':spec['url']}])
    page=context.new_page(); errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(spec['url']+'/admin',wait_until='domcontentloaded')
    expect(page.locator('#operations-status')).to_have_text('Running with limited capabilities')
    expect(page.locator('#operations-checks')).to_contain_text('down')
    expect(page.locator('#operations-issues')).to_contain_text('Docker is unavailable')
    expect(page.locator('#operations-failures')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#operations-failures img').count()==0
    assert 'private-fixture-result' not in page.locator('#operations-dashboard').inner_text()
    screenshots={'operations-desktop':base64.b64encode(page.screenshot()).decode()}
    page.route('**/api/admin/health',lambda route:route.fulfill(status=503,json={'error':'unavailable'}))
    page.locator('#operations-refresh').click()
    expect(page.locator('#operations-status')).to_have_text('Health check unavailable. Retry shortly.')
    expect(page.locator('#operations-refresh')).to_be_enabled()
    page.unroute('**/api/admin/health')
    page.locator('#operations-refresh').click()
    expect(page.locator('#operations-status')).to_have_text('Running with limited capabilities')
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('#operations-dashboard').bounding_box()['width']<=390
    screenshots['operations-mobile']=base64.b64encode(page.screenshot()).decode()
    page.goto(spec['url'],wait_until='domcontentloaded')
    expect(page.locator('#runtime-system-state')).to_have_text('Some features unavailable')
    expect(page.locator('#runtime-notice')).to_contain_text('Docker runner is unavailable')
    expect(page.locator('#btn-terminal-toggle')).to_be_disabled()
    expect(page.locator('#nav-preview')).to_be_disabled()
    expect(page.locator('#nav-files')).to_be_enabled()
    context.request.get(spec['url']+'/__fixture/recover')
    page.reload()
    expect(page.locator('#runtime-system-state')).to_have_text('Chat and runner ready')
    expect(page.locator('#btn-terminal-toggle')).to_be_enabled()
    expect(page.locator('#runtime-notice')).to_be_hidden()
    expect(page.locator('#build-run-status')).to_contain_text('Build blocked')
    expect(page.locator('#build-run-status')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#build-run-status img').count()==0
    phase={'completed':False}
    def resume(route):
        phase['completed']=True
        route.fulfill(status=202,json={'query_id':'build-ui-fixture'})
    page.route('**/api/chats/1/build-run/resume',resume)
    page.route('**/api/chats/1/build-run',lambda route:route.fulfill(json={
        'status':'completed' if phase['completed'] else 'blocked','steps':14,'checkpoints':1,
        'detail':'Fixture build state','can_resume':not phase['completed']}))
    event={'type':'message','id':99999,'text':'Saved final build reply without token events.','build_status':'completed'}
    page.route('**/api/stream/build-ui-fixture*',lambda route:route.fulfill(
        content_type='text/event-stream',body='data: '+json.dumps(event)+'\n\n'))
    page.get_by_role('button',name='Resume build',exact=True).click()
    expect(page.locator('.msg[data-id="99999"]')).to_contain_text('Saved final build reply without token events.')
    assert page.locator('.msg[data-id="99999"]').count()==1
    expect(page.locator('#build-run-status')).to_contain_text('Build completed')
    assert page.locator('#build-run-status').bounding_box()['width']<=390
    screenshots['operations-build-resume']=base64.b64encode(page.screenshot()).decode()
    assert not errors,errors
    browser.close()
    print(json.dumps({'passed':True,'screenshots':screenshots}))
'''


def main():
    mode = {'docker': 'down'}
    originals = {name: getattr(A, name) for name in ('_dependency_checks', 'user_gemini_key', 'shared_gemini_keys',
                'openrouter_keys', 'tavily_keys', 'user_disk_usage', 'selectable_models')}
    status = A.KEY_MANAGER.status
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        application = A.create_app({'TESTING': True, 'BACKGROUND_THREADS': False, 'SECRET_KEY': 'operations-browser-fixture',
            'SESSION_COOKIE_NAME': 'stellar_session_main', 'SESSION_COOKIE_SECURE': False, 'CSRF_TOKENS': False,
            'DATABASE': str(root / 'fixture.db'), 'OUTPUTS_DIR': str(root / 'outputs'),
            'SANDBOX_DIR': str(root / 'labs'), 'DEPLOYMENTS_DIR': str(root / 'projects')})
        with application.app_context():
            A.init_db()
            db = A.get_db()
            db.execute("INSERT INTO users(id,username,password_hash,is_approved,is_admin) VALUES(1,'fixture@example.org','',1,1)")
            db.execute("INSERT INTO chats(id,user_id,name) VALUES(1,1,'Operations fixture')")
            db.execute("INSERT INTO tool_calls(chat_id,tool_name,arguments,result,duration_ms,is_error) VALUES(1,?, '{}','private-fixture-result',9,1)", ('<img src=x onerror=alert(1)>',))
            db.commit()
            from agent_workflow import BuildRun
            build=BuildRun(db,'fixture-build',1,1,'Build and deploy a fixture application')
            build.update('blocked','Provider unavailable <img src=x onerror=alert(1)>')
        A._dependency_checks = lambda: {'database': 'ok', 'redis': 'ok', 'docker': mode['docker']}
        A.user_gemini_key = lambda *args: 'fixture-configured-key'
        A.shared_gemini_keys = lambda: []
        A.openrouter_keys = lambda: []
        A.tavily_keys = lambda: []
        A.user_disk_usage = lambda *args: 0
        A.selectable_models = lambda: [A.DEFAULT_MODEL]
        A.KEY_MANAGER.status = lambda *args: [{'usable': True}]

        @application.get('/__fixture/recover')
        def recover():
            mode['docker'] = 'ok'
            return {'ok': True}

        client = application.test_client()
        with client.session_transaction() as session:
            session['user_id'] = 1
            session['epoch'] = 0
        cookie = client.get_cookie('stellar_session_main').value
        server = make_server('127.0.0.1', 0, application, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        container = None
        try:
            container = docker.from_env(timeout=120).containers.run('stellar-browser:1.63.0',
                command=['python', '-c', SCRIPT, json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'cookie': cookie})],
                detach=True, network_mode='host', labels={'stellar': 'operations-ui-fixture'})
            result = container.wait(timeout=120)
            if result['StatusCode']:
                raise AssertionError(container.logs(stderr=True, stdout=False).decode()[-5000:])
            report = json.loads(container.logs(stdout=True, stderr=False))
            output = Path(__file__).resolve().parents[1] / '.test-tmp'
            output.mkdir(exist_ok=True)
            for name, data in report['screenshots'].items():
                path = output / (name + '.png')
                path.write_bytes(base64.b64decode(data))
                print('PASS operations UI:', path)
            print('PASS health failure/retry, safe rendering, mobile layout and runner recovery')
        finally:
            if container is not None:
                container.remove(force=True)
            server.shutdown()
            server.server_close()
            A.KEY_MANAGER.status = status
            for name, value in originals.items():
                setattr(A, name, value)


if __name__ == '__main__':
    main()
