"""Bounded, checkpointed build runs. Never replays a stored shell command."""
import json
import re
import time
import hashlib

BUILD_STEPS = 96
BUILD_SECONDS = 30 * 60
CHECKPOINT_STEPS = 8


def is_build_task(text):
    text = (text or '').strip().lower()
    action_request = re.match(r'^(?:please\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:build|implement|create|make|finish|continue|redesign|develop)\b', text)
    if not action_request and re.match(r'^(how|why|what|can|could|would|will|explain|tell me|say)\b', text):
        return False
    return bool(re.search(r'\b(build|implement|create|make|finish|continue|redesign|develop)\b', text)
                and re.search(r'\b(deploy|deployment|application|app|website|dashboard|frontend)\b', text))


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS agent_build_runs (
        query_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        request TEXT NOT NULL, status TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
        checkpoints INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL DEFAULT '{}',
        detail TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
        FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
    db.commit()


class BuildRun:
    def __init__(self, db, query_id, chat_id, user_id, request, evidence=None):
        self.db, self.id = db, query_id
        self.started = time.monotonic()
        self.evidence = evidence if evidence is not None else {'projects': {}, 'tests': False, 'quality_version': 1}
        self.steps = 0
        self.request = request
        ensure(db)
        now = time.time()
        db.execute('INSERT INTO agent_build_runs(query_id,chat_id,user_id,request,status,evidence,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                   (query_id,chat_id,user_id,request,'running',json.dumps(self.evidence),now,now))
        db.commit()

    def update(self, status='running', detail=''):
        self.db.execute('UPDATE agent_build_runs SET status=?,steps=?,checkpoints=?,evidence=?,detail=?,updated=? WHERE query_id=?',
                        (status,self.steps,self.steps//CHECKPOINT_STEPS,json.dumps(self.evidence),detail[:2000],time.time(),self.id))
        self.db.commit()

    def advance(self):
        self.steps += 1
        self.update(detail='Continuing the build from saved tool results.')

    def exhausted(self):
        return self.steps >= BUILD_STEPS or time.monotonic()-self.started >= BUILD_SECONDS

    def observe(self, name, args, result, failed):
        operation = {k:v for k,v in args.items() if k != 'status'}
        fingerprint = hashlib.sha256((name + json.dumps(operation, sort_keys=True, default=str)).encode()).hexdigest()
        failures = self.evidence.setdefault('failures', {})
        if failed:
            failures[fingerprint] = min(failures.get(fingerprint, 0) + 1, 100)
            if len(failures) > 32:
                del failures[next(iter(failures))]
        else:
            failures.pop(fingerprint, None)
        if name == 'build_plan' and not failed:
            from build_quality import validate_plan
            # Revalidate arguments on the producer thread; no DB crosses tool threads.
            self.evidence['plan'] = validate_plan(args.get('plan'))
            self.update()
            return
        project = str(args.get('app_id') or '')
        if name == 'design_research':
            self.evidence['design_research'] = {'attempted': True, 'available': not failed}
        if name == 'browser_test' and project in self.evidence.get('projects', {}):
            self.evidence['projects'][project].setdefault('browser', {}).pop(
                'mobile' if args.get('mobile') is True else 'desktop', None)
        if name == 'repo_control' and project in self.evidence.get('projects',{}):
            previous = self.evidence['projects'][project]
            if args.get('action') in ('execute','serve','restore','restart','stop','rename','deploy'):
                previous['verified'] = False
                previous['browser'] = {}
            if args.get('action') in ('execute','restore','deploy'):
                previous['tests'] = False
            if args.get('action') in ('execute','restore','serve','deploy'):
                previous['served'] = False
        if failed:
            self.evidence['last_tool_error'] = {'tool':name,'action':args.get('action','')}
            self.update(detail='A tool failed; the agent must inspect the result before retrying.')
            return
        if self.evidence.get('last_tool_error') == {'tool':name,'action':args.get('action','')}:
            self.evidence.pop('last_tool_error', None)
        if name == 'browser_test' and project in self.evidence.get('projects', {}):
            try:
                report = json.loads(result)
                if isinstance(report, dict) and report.get('passed') is True:
                    self.evidence['projects'][project].setdefault('browser', {})[
                        'mobile' if args.get('mobile') is True else 'desktop'] = True
            except (TypeError, ValueError):
                pass
            self.update()
            return
        if name != 'repo_control':
            self.update()
            return
        action = args.get('action')
        projects = self.evidence.setdefault('projects',{})
        if action == 'deploy':
            match = re.search(r'Process ID\*\*: `([a-f0-9]{12})`', result)
            if match: projects[match.group(1)] = {'served':False,'verified':False,'tests':False}
        if project:
            item = projects.setdefault(project, {'served':False,'verified':False,'tests':False})
            if action == 'serve': item['served'],item['verified'] = True,False
            if action in ('execute','restore','restart','stop'): item['verified'] = False
            if action in ('execute','restore'): item['served'] = False
            if action == 'verify' and result.startswith('VERIFIED DEPLOYMENT:'): item['verified'] = True
            if action == 'execute' and not args.get('background'):
                command = args.get('command','')
                # Only evidence of an executed test command, not the string
                # "pytest" embedded in a heredoc which merely writes a file.
                if re.fullmatch(r'\s*(?:python(?:3)?\s+-m\s+(?:unittest|pytest)|pytest|python(?:3)?\s+[\w./-]*test[\w./-]*\.py|npm\s+(?:run\s+)?test)(?:\s+[\w./=:-]+)*\s*', command):
                    item['tests'] = bool(re.search(r'\bRan\s+[1-9]\d*\s+tests?\b|\b[1-9]\d*\s+passed\b|#\s*pass\s+[1-9]\d*\b', result, re.I)) and not bool(re.search(
                        r'\bFAILED\s*\(|\b[1-9]\d*\s+(?:failed|errors?)\b|#\s*fail\s+[1-9]\d*\b', result, re.I))
                    self.evidence['tests'] = any(p.get('tests') for p in projects.values())
                else:
                    item['tests'] = False
            elif action in ('execute','restore'):
                item['tests'] = False
        self.update()

    def missing(self):
        projects = self.evidence.get('projects',{})
        missing=[]
        if self.evidence.get('quality_version') and not self.evidence.get('plan'):
            missing.append('record requirements, acceptance checks and design direction with build_plan')
        if self.evidence.get('plan',{}).get('interface') == 'web' and not self.evidence.get('design_research',{}).get('attempted'):
            missing.append('attempt design_research for relevant UI references; disclose inaccessible sources')
        if not projects: return missing + ['create or identify the application project']
        for project, data in projects.items():
            if not data.get('tests'): missing.append(f'{project}: execute the project tests in a foreground command')
            if not data.get('served'): missing.append(f'{project}: save and start the server with repo_control serve')
            if not data.get('verified'): missing.append(f'{project}: check public HTTP and same-origin assets with repo_control verify')
            if self.evidence.get('browser_available') and self.evidence.get('plan',{}).get('interface') == 'web':
                for viewport in ('desktop', 'mobile'):
                    if not data.get('browser',{}).get(viewport):
                        missing.append(f'{project}: run browser_test for the primary journey on {viewport}')
        return missing

    def context_note(self):
        """Small factual checkpoint, not a replacement for tool results.

        Repeated after tool rounds so long builds keep their original objective
        and verification gaps in view. Does not contain credentials or commands.
        """
        remaining = self.missing()
        last = self.evidence.get('last_tool_error')
        note = ('BUILD CHECKPOINT (execution evidence only):\nOriginal request: '
                + self.request[:5000] + '\nOutstanding verification: '
                + ('; '.join(remaining) if remaining else 'Basic test/serve/public checks recorded. '
                   'Still check the original functional requirements and report verification gaps.')
                + '\nUse existing project files. Inspect failures before editing; do not recreate the app. '
                'A smoke check does not prove authentication, database persistence or browser behavior.')
        if last:
            note += '\nLast failed tool/action: ' + str(last.get('tool',''))[:80] + '/' + str(last.get('action',''))[:80]
        if any(count >= 3 for count in self.evidence.get('failures',{}).values()):
            note += '\nREPEATED FAILURE: an identical operation has failed at least three times. Inspect logs and change the approach before retrying. Preserve files; report an external blocker when applicable.'
        if self.evidence.get('plan'):
            note += '\nBuild brief (NOT verification evidence): ' + json.dumps(self.evidence['plan'], ensure_ascii=False)
            if self.evidence['plan']['interface'] == 'web' and not self.evidence.get('browser_available'):
                note += '\nBrowser checks unavailable: disclose that interactive and visual browser verification were not performed by this agent.'
        return note


def latest(db, chat_id, user_id):
    ensure(db)
    row=db.execute('SELECT query_id,status,steps,checkpoints,evidence,detail,updated FROM agent_build_runs WHERE chat_id=? AND user_id=? ORDER BY created DESC LIMIT 1', (chat_id,user_id)).fetchone()
    if not row: return None
    result=dict(row)
    result['evidence']=json.loads(result['evidence'])
    if ((result['status']=='running' and time.time()-result['updated']>BUILD_SECONDS+600)
            or (result['status']=='resuming' and time.time()-result['updated']>60)):
        result['status']='interrupted'
        result['detail']='The worker stopped reporting progress. Review saved work before resuming.'
    result['can_resume']=result['status'] in ('blocked','paused','interrupted')
    return result
