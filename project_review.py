"""Bounded static preflight for common Python session-secret mistakes.

Does not import or execute project source and is not a comprehensive audit.
Findings contain locations and remediation, never secret values/source lines.
"""
import ast
import json
import os
from pathlib import Path


def literal_secret(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, bytes)) and bool(node.value)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(literal_secret(value) for value in node.values)
    if isinstance(node, ast.Call):
        env_get = (isinstance(node.func, ast.Attribute)
                   and ((node.func.attr == 'get' and isinstance(node.func.value, ast.Attribute)
                         and node.func.value.attr == 'environ') or node.func.attr == 'getenv'))
        if env_get:
            return ((len(node.args) > 1 and literal_secret(node.args[1]))
                    or any(k.arg == 'default' and literal_secret(k.value) for k in node.keywords))
    return False


def inspect_source(text, filename):
    tree = ast.parse(text)
    findings = []
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign):
            pairs = [(node.target, node.value)]
        for target, value in pairs:
            key = (target.slice.value if isinstance(target, ast.Subscript)
                   and isinstance(target.slice, ast.Constant) else
                   target.attr if isinstance(target, ast.Attribute) else
                   target.id if isinstance(target, ast.Name) else '')
            if str(key).lower() in ('secret_key', 'session_secret', 'session_key') and literal_secret(value):
                findings.append({'file':filename,'line':node.lineno,'code':'literal-session-secret',
                                 'fix':'Replace the fixed session-signing secret/default with a randomly generated secret persisted outside source, or a required environment secret. Keep it stable across restarts. Add a regression test.'})
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == 'SECRET_KEY' and literal_secret(keyword.value):
                    findings.append({'file':filename,'line':node.lineno,'code':'literal-session-secret',
                                     'fix':'Remove the fixed SECRET_KEY/default. Use a stable private random secret and test restart persistence.'})
    return findings


def inspect_test_source(text, filename):
    """Catch tests appended after unittest.main, which exits before them."""
    tree = ast.parse(text)
    findings = []
    for guard in ast.walk(tree):
        if not isinstance(guard, ast.If) or not any(isinstance(n,ast.Name) and n.id=='__name__' for n in ast.walk(guard.test)):
            continue
        main_seen = False
        for statement in guard.body:
            if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call):
                call=statement.value
                if (isinstance(call.func,ast.Attribute) and call.func.attr=='main'
                        and isinstance(call.func.value,ast.Name) and call.func.value.id=='unittest'
                        and not any(k.arg=='exit' and isinstance(k.value,ast.Constant) and k.value.value is False for k in call.keywords)):
                    main_seen=True
            if main_seen and isinstance(statement,(ast.FunctionDef,ast.AsyncFunctionDef)) and statement.name.startswith('test_'):
                findings.append({'file':filename,'line':statement.lineno,'code':'undiscovered-test-definition',
                                 'fix':'This test is defined after unittest.main() under the script guard. Put it in a discoverable TestCase or test module, rerun discovery, and confirm the new test actually executes.'})
    return findings


def review_tree(root):
    findings, files, tests, total = [], 0, 0, 0
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if d not in
                           ('.git','.stellar','.venv','venv','node_modules','__pycache__')
                           and not Path(directory,d).is_symlink())
        for name in sorted(names):
            path = Path(directory,name)
            if not name.endswith('.py') or path.is_symlink():
                continue
            files += 1
            size = path.stat().st_size
            total += size
            if files > 300 or size > 1024*1024 or total > 5*1024*1024:
                raise ValueError('Python source preflight exceeds its bounded scan size; split the project or review explicitly.')
            filename = str(path.relative_to(root))
            try:
                source=path.read_text(encoding='utf-8')
                if name.startswith('test_') or 'tests' in path.relative_to(root).parts:
                    tests+=1
                    findings.extend(inspect_test_source(source,filename))
                else:
                    findings.extend(inspect_source(source,filename))
            except SyntaxError as exc:
                findings.append({'file':filename,'line':exc.lineno,'code':'python-syntax',
                                 'fix':'Repair this Python syntax error and rerun tests.'})
    return {'python_files':files,'test_files':tests,'findings':findings,
            'scope':'Static Python literal session-secret, syntax and unreachable unittest checks only; not a security certification.'}


def review_container(container):
    script = Path(__file__).read_text(encoding='utf-8')
    result = container.exec_run(['timeout','15','python3','-c',script],workdir='/app')
    if result.exit_code:
        raise ValueError('Python source preflight could not finish. Inspect source size, syntax and Python availability.')
    output = result.output or b''
    if len(output) > 128*1024:
        raise ValueError('Too many preflight findings; repair the project before verifying.')
    return json.loads(output)


if __name__ == '__main__':
    print(json.dumps(review_tree('/app')))
