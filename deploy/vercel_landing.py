"""Export the two landing pages as two static sites for Vercel.

    python deploy/vercel_landing.py      -> deploy/vercel/petrova/  (the crimson
                                            Petrova-lines page, /welcome)
                                         -> deploy/vercel/cosmos/   (the wormhole
                                            and Gargantua page, /cosmos)

Neither page needs a server: each is one HTML file plus its scripts,
styles, font and images (everything moving on them is drawn by WebGL in
the browser). This renders each once, as an anonymous visitor sees it, but
without the links into the app (sign up, sign in, and the cosmos page's
link to the other edition, which is its own site); copies only the files
it uses (tracked in git, so nothing stray goes out); and writes each a
vercel.json with the security headers the app sends.

Run it again after changing a page, then commit deploy/vercel/. Each folder
is its own Vercel project: Root Directory deploy/vercel/petrova or
deploy/vercel/cosmos, framework "Other", no build command (see
deploy/deploy_guide.md, "The landing pages on Vercel").
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "deploy", "vercel")

# site folder -> (template, what it loads: folders and single files)
SITES = {
    "petrova": ("landing.html", ["static/landing/", "static/favicon.svg"]),
    "cosmos": ("cosmos.html", ["static/cosmos/", "static/landing/fonts/montserrat-latin-var.woff2",
                               "static/favicon.svg"]),
}


def render_pages():
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    # a throwaway database: rendering reads nothing from it, but the app's
    # request hooks expect one to exist
    os.environ["DATABASE_NAME"] = os.path.join(tempfile.mkdtemp(), "export.db")
    import app as A
    from flask import g, render_template
    application = A.create_app({"BACKGROUND_THREADS": False, "TESTING": True})
    with application.app_context():
        A.init_db()
    pages = {}
    for site, (template, _) in SITES.items():
        with application.test_request_context("/"):
            application.preprocess_request()
            g.user = None
            pages[site] = render_template(template, static_export=True)
    return pages, A.APP_CSP


def tracked(paths):
    out = subprocess.run(["git", "ls-files", "--", *paths], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    # the dev harness (_dev.*) is never tracked, but be sure
    return [p for p in out if p and not os.path.basename(p).startswith("_")]


def check(site_dir, html):
    """Every local file the page or its stylesheets link to is in the copy,
    and no link into the app is left."""
    problems = []
    for ref in set(re.findall(r'(?:href|src)="(/static/[^"#?]+)"', html)):
        if not os.path.isfile(os.path.join(site_dir, ref.lstrip("/"))):
            problems.append("missing " + ref)
    for dirpath, _, names in os.walk(site_dir):
        for name in names:
            if name.endswith(".css"):
                css = open(os.path.join(dirpath, name), encoding="utf-8").read()
                for ref in re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", css):
                    if ref.startswith(("data:", "http", "#")):
                        continue
                    if not os.path.isfile(os.path.normpath(os.path.join(dirpath, ref))):
                        problems.append(f"missing {ref} (from {name})")
    for link in re.findall(r'href="(/(?!static/)[^"]*)"', html):
        problems.append("app link left: " + link)
    return problems


def main():
    pages, csp = render_pages()
    headers = [{
        "source": "/(.*)",
        "headers": [
            {"key": "Content-Security-Policy", "value": csp},
            {"key": "X-Frame-Options", "value": "DENY"},
            {"key": "X-Content-Type-Options", "value": "nosniff"},
            {"key": "Referrer-Policy", "value": "same-origin"},
        ],
    }]
    failed = False
    for site, (_, paths) in SITES.items():
        site_dir = os.path.join(OUT, site)
        # start clean, so files a page no longer uses do not linger
        if os.path.isdir(site_dir):
            shutil.rmtree(site_dir)
        os.makedirs(site_dir)
        files = tracked(paths)
        for rel in files:
            dst = os.path.join(site_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, rel), dst)
        with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(pages[site])
        with open(os.path.join(site_dir, "vercel.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cleanUrls": True, "headers": headers}, f, indent=2)
            f.write("\n")
        problems = check(site_dir, pages[site])
        size = sum(os.path.getsize(os.path.join(d, n)) for d, _, ns in os.walk(site_dir) for n in ns)
        status = "ok" if not problems else "PROBLEMS: " + "; ".join(problems)
        print(f"{os.path.relpath(site_dir, ROOT)}: {len(files)} files, {size / 1024:.0f} KB - {status}")
        failed = failed or bool(problems)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
