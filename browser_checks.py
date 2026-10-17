"""Disposable browser runner orchestration; never executes model-supplied code."""
import base64
import json
import secrets
import time

MAX_STEPS = 12
MAX_REPORT = 3 * 1024 * 1024
BROWSER_IMAGE = "stellar-browser:1.63.0"


def docker_client():
    import docker
    return docker.from_env(timeout=10)


def cleanup_stale_runs(client, scope):
    """Recover runs left behind by a worker crash, scoped to this instance."""
    filters = {"label": ["stellar=browser-check", "stellar.instance=" + scope]}
    for resource in client.containers.list(all=True, filters=filters):
        expiry = (resource.labels or {}).get("expires", "")
        if expiry.isdigit() and int(expiry) < time.time():
            resource.remove(force=True)
    for resource in client.networks.list(filters=filters):
        expiry = (resource.attrs.get("Labels") or {}).get("expires", "")
        if expiry.isdigit() and int(expiry) < time.time():
            # A stale network may still include the target application.
            for container_id in (resource.attrs.get("Containers") or {}):
                resource.disconnect(container_id, force=True)
            resource.remove()


def validate_steps(steps):
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError("steps must be an array containing at most 12 actions.")
    allowed = {
        "click": {"action", "selector"},
        "fill": {"action", "selector", "value"},
        "expect_text": {"action", "text"},
        "expect_visible": {"action", "selector"},
        "expect_title": {"action", "text"},
    }
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise ValueError("Each step must be an action object.")
        fields = allowed.get(step["action"])
        if fields is None or set(step) != fields:
            raise ValueError("Use click/fill/expect_text/expect_visible/expect_title with their required fields.")
        if any(not isinstance(value, str) or len(value) > 2000 for value in step.values()):
            raise ValueError("Step values must be strings of at most 2000 characters.")
        if "selector" in step and (not step["selector"].strip() or len(step["selector"]) > 300):
            raise ValueError("Selectors must contain 1–300 characters.")
    return steps


def validate_path(path):
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
            or "\\" in path or len(path) > 2000 or any(ord(c) < 32 for c in path)
            or "#" in path):
        raise ValueError("path must be an application-relative path beginning with one slash.")
    return path


def run_check(client, target, port, steps, path, mobile=False, image=BROWSER_IMAGE, scope="stellar"):
    """One internal network and runner per call; always remove both.

    The target is already ownership-checked by the caller. Only this container
    is attached to the temporary network; the browser gets no default egress.
    No host directory, Docker socket or application volume is mounted.
    """
    validate_steps(steps)
    validate_path(path)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid application port.")
    ident = secrets.token_hex(8)
    name = "stellar-browser-" + ident
    labels = {"stellar": "browser-check", "run": ident,
              "stellar.instance": scope, "expires": str(int(time.time()) + 300)}
    network = runner = None
    connected = False
    try:
        cleanup_stale_runs(client, scope)
        network = client.networks.create(name, internal=True, check_duplicate=True, labels=labels)
        connected = True
        network.connect(target, aliases=["project"])
        payload = base64.b64encode(json.dumps({
            "origin": f"http://project:{port}", "path": path,
            "steps": steps, "mobile": mobile,
        }).encode()).decode()
        runner = client.containers.run(image,
            command=["python", "/opt/stellar/browser_worker.py", payload],
            name=name, network=network.name,
            detach=True, user="pwuser", read_only=True,
            tmpfs={"/tmp": "rw,nosuid,size=268435456", "/home/pwuser": "rw,nosuid,size=33554432"},
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            mem_limit="1g", nano_cpus=1_000_000_000, pids_limit=256,
            shm_size="256m", init=True,
            labels=labels)
        status = runner.wait(timeout=100)
        # logs() could otherwise allocate unbounded output from a failed runner.
        raw = runner.logs(stdout=True, stderr=False, tail=1)
        if len(raw) > MAX_REPORT:
            raise RuntimeError("Browser report exceeded its size limit.")
        if status.get("StatusCode") != 0:
            raise RuntimeError("Browser runner failed. Check that the browser image is built.")
        report = json.loads(raw)
        if not isinstance(report, dict) or type(report.get("passed")) is not bool:
            raise RuntimeError("Browser runner returned an invalid report.")
        return report
    finally:
        # Docker can create a resource and then time out before returning its
        # id. Recover only our unguessable name and matching run label.
        if runner is None:
            try:
                candidate = client.containers.get(name)
                if (candidate.labels or {}).get("run") == ident:
                    runner = candidate
            except Exception:
                pass
        if network is None:
            try:
                candidate = client.networks.get(name)
                if (candidate.attrs.get("Labels") or {}).get("run") == ident:
                    network = candidate
                    connected = True
            except Exception:
                pass
        if runner is not None:
            try:
                runner.remove(force=True)
            except Exception:
                pass
        if connected:
            try:
                network.disconnect(target, force=True)
            except Exception:
                pass
        if network is not None:
            try:
                network.remove()
            except Exception:
                pass
