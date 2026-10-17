"""Shared preview configuration and narrowly scoped Redis capabilities."""
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit

PROJECT_ID = re.compile(r"[0-9a-f]{12}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
TICKET_TTL = 45
SESSION_TTL = 900


def origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname)):
            raise ValueError()
        port = parsed.port
        host = parsed.hostname
        if len(host) > 220 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.split(".")):
            raise ValueError()
        local = host == "localhost" or host.endswith(".localhost")
        if parsed.scheme != "https" and not local:
            raise ValueError()
        authority = host + (f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else "")
        return f"{parsed.scheme}://{authority}"
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Use an HTTPS origin without a path (HTTP is allowed only on localhost).") from None


@dataclass(frozen=True)
class PreviewConfig:
    base: str
    parent: str
    scope: str

    @classmethod
    def from_mapping(cls, config):
        base = origin(config.get("STELLAR_PREVIEW_ORIGIN", ""))
        parent = origin(config.get("STELLAR_APP_ORIGIN", ""))
        b, p = urlsplit(base).hostname, urlsplit(parent).hostname
        if b == p or b.endswith("." + p) or p.endswith("." + b):
            raise ValueError("Preview and Stellar must use separate, non-overlapping hostnames.")
        scope = hashlib.sha256(str(config["DATABASE"]).encode()).hexdigest()[:16]
        return cls(base, parent, scope)

    def project_origin(self, project_id):
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise ValueError("Invalid project id.")
        parsed = urlsplit(self.base)
        return f"{parsed.scheme}://p-{project_id}.{parsed.netloc}"

    @property
    def frame_source(self):
        parsed = urlsplit(self.base)
        return f"{parsed.scheme}://*.{parsed.netloc}"

    @property
    def cookie_name(self):
        return "__Host-stellar_preview" if self.base.startswith("https:") else "stellar_preview_local"

    def key(self, kind, token):
        return f"preview:{self.scope}:{kind}:{token}"


def issue_ticket(redis, config, user_id, project_id, epoch):
    target = config.project_origin(project_id)
    token = secrets.token_urlsafe(32)
    redis.setex(config.key("ticket", token), TICKET_TTL, json.dumps({
        "user_id": int(user_id), "project_id": project_id, "epoch": int(epoch),
    }))
    return target + "/__stellar_preview/launch?ticket=" + token


def consume_ticket(redis, config, token):
    if not TOKEN.fullmatch(token or ""):
        return None
    value = redis.getdel(config.key("ticket", token))
    return json.loads(value) if value else None
