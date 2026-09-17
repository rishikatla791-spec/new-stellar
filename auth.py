"""Authentication: register, login, logout, and the approval gate.

Phase 1 uses local username + password. The original Stellar uses Google
OAuth via Firebase, which is the right answer in production but would mean
configuring a Firebase project before you can render a single page. Phase 8
swaps this out; everything downstream depends only on ``session["user_id"]``,
so that swap touches this file and nothing else.
"""

from __future__ import annotations

import functools
import sqlite3

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.before_app_request
def load_logged_in_user() -> None:
    """Populate g.user once per request, before any view runs.

    Views and templates read g.user rather than re-querying. g is cleared
    between requests, so there is no risk of leaking one user's row into
    another's request.
    """
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return

    g.user = get_db().execute(
        "SELECT id, username, display_name, is_approved, is_admin"
        " FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    # The row can be gone if the account was deleted while the cookie lived
    # on. Clearing the session turns a confusing 500 into a clean redirect.
    if g.user is None:
        session.clear()


def login_required(view):
    """Redirect anonymous visitors to the login page."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def require_approval(view):
    """Gate a view on an approved account.

    Stellar hands out container shells and API quota, so registration alone
    must not grant access. Every route that spends resources wears this.
    """
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return {"error": "Authentication required"}, 401
            return redirect(url_for("auth.login", next=request.path))
        if not g.user["is_approved"]:
            return {"error": "Your account is awaiting approval."}, 403
        return view(**kwargs)
    return wrapped


@bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        display_name = (request.form.get("display_name") or "").strip() or None

        error = None
        if not username:
            error = "Email is required."
        elif "@" not in username:
            error = "That does not look like an email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."

        if error is None:
            database = get_db()
            # The very first account to register owns the instance, so it is
            # auto-approved and made admin. Otherwise there would be nobody
            # with the authority to approve anyone, including themselves.
            is_first = database.execute(
                "SELECT COUNT(*) AS n FROM users"
            ).fetchone()["n"] == 0

            try:
                database.execute(
                    "INSERT INTO users"
                    " (username, password_hash, display_name, is_approved, is_admin)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        username,
                        # scrypt by default in Werkzeug 3: salted, and
                        # deliberately slow to make brute force expensive.
                        generate_password_hash(password),
                        display_name,
                        1 if is_first else 0,
                        1 if is_first else 0,
                    ),
                )
                database.commit()
            except sqlite3.IntegrityError:
                # UNIQUE constraint on username. Catching the database error
                # rather than pre-checking avoids a race between two
                # simultaneous registrations of the same address.
                error = f"{username} is already registered."
            else:
                if is_first:
                    flash("Account created and approved - you are the admin.")
                else:
                    flash("Account created. An admin must approve it before you can chat.")
                return redirect(url_for("auth.login"))

        flash(error)

    return render_template("login.html", mode="register")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        # One message for both failure modes. Saying "no such user" would
        # let an attacker enumerate which addresses are registered.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.")
        else:
            # Rotate the session id on privilege change, so a fixed
            # pre-login cookie cannot be reused as an authenticated one.
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = user["display_name"] or user["username"]
            session.permanent = True

            nxt = request.args.get("next")
            # Only accept relative paths. An absolute URL here would be an
            # open redirect, handing attackers a phishing vector.
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("index"))

    return render_template("login.html", mode="login")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
