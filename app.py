"""Stellar - application factory.

Run in development:

    .venv/Scripts/python.exe app.py

The factory pattern (create_app() returning a configured instance, rather
than a module-level ``app = Flask(__name__)``) exists so that tests can
build a throwaway app pointing at a temporary database. With a module-level
app, importing the module is what configures it, and tests are stuck with
whatever the import decided.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, session, url_for

import db

PROJECT_ROOT = Path(__file__).parent


def create_app(test_config: dict | None = None) -> Flask:
    # load_dotenv does not overwrite variables that already exist in the
    # environment, so a real deployment can override keys.env by exporting
    # values, without editing the file.
    load_dotenv(PROJECT_ROOT / "keys.env")

    app = Flask(__name__, instance_relative_config=False)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=str(PROJECT_ROOT / os.environ.get("DATABASE_NAME", "stellar_local.db")),
        # Distinct cookie name so a locally-running Stellar does not collide
        # with other Flask apps on localhost, which all default to "session".
        SESSION_COOKIE_NAME="stellar_session_main",
        # Defence in depth against session-cookie theft and CSRF:
        SESSION_COOKIE_HTTPONLY=True,   # JavaScript cannot read the cookie
        SESSION_COOKIE_SAMESITE="Lax",  # not sent on cross-site POSTs
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,  # 50MB, matching nginx (phase 9)
    )

    if test_config:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Copy keys.env.example to keys.env "
            "and generate one:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"'
        )

    db.register(app)

    import auth
    import chat

    app.register_blueprint(auth.bp)
    app.register_blueprint(chat.bp)

    @app.route("/")
    def index():
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return render_template("index.html", display_name=session.get("display_name"))

    @app.route("/healthz")
    def healthz():
        """Liveness probe. Phase 9's nginx and systemd both poll this."""
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    application = create_app()

    # Create the database on first run so there is no separate setup step.
    with application.app_context():
        if not Path(application.config["DATABASE"]).exists():
            db.init_db()
            print(f"  Created {application.config['DATABASE']}")

    # debug=True enables the reloader and the interactive traceback page.
    # Never use this server in production - phase 9 switches to Gunicorn.
    application.run(host="127.0.0.1", port=5000, debug=True)
