"""The Linux branch of the sandbox file helpers, against real symlinks.

    python tests/sandbox_paths_linux.py      (on Linux, in the project venv)

On Linux the helpers open every folder with O_NOFOLLOW relative to the
previous one, so a link planted by code in the sandbox can never be
followed. Windows takes a different branch, which smoke_test.py covers
through Docker; this file covers the branch production actually runs.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as A  # noqa: E402

ok = True


def check(label, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + label)
    ok &= bool(cond)


check("fd-based branch is active on Linux", A._FD_SAFE)
root = Path(tempfile.mkdtemp())
ws = root / "ws"
target = root / "outside"
ws.mkdir()
target.mkdir()
(target / "secret.txt").write_text("host secret")

# Plain operation works.
A.sandbox_write(ws, "uploads/a.txt", b"hello")
check("write creates folders and the file", (ws / "uploads" / "a.txt").read_bytes() == b"hello")
try:
    A.sandbox_write(ws, "uploads/a.txt", b"again")
    check("exclusive write refuses an existing name", False)
except FileExistsError:
    check("exclusive write refuses an existing name", True)
fh, size = A.sandbox_open(ws, "uploads/a.txt")
with fh:
    check("open reads a regular file", fh.read() == b"hello" and size == 5)
check("unlink removes a file", A.sandbox_unlink(ws, "uploads/a.txt") and not (ws / "uploads" / "a.txt").exists())
check("unlink of a missing file is False", A.sandbox_unlink(ws, "uploads/a.txt") is False)

# Folder link planted by the sandbox.
os.rmdir(ws / "uploads")
os.symlink("../outside", ws / "uploads")
for label, fn in [
    ("write refuses a linked folder", lambda: A.sandbox_write(ws, "uploads/evil.txt", b"x")),
    ("ensure_dir refuses a linked folder", lambda: A.sandbox_ensure_dir(ws, "uploads")),
    ("open refuses through a linked folder", lambda: A.sandbox_open(ws, "uploads/secret.txt")),
    ("unlink refuses through a linked folder", lambda: A.sandbox_unlink(ws, "uploads/secret.txt")),
]:
    try:
        fn()
        check(label, False)
    except A.SandboxPathError:
        check(label, True)
check("nothing was written outside", not (target / "evil.txt").exists())
check("nothing was deleted outside", (target / "secret.txt").exists())

# File link planted by the sandbox.
os.symlink("../outside/secret.txt", ws / "k.txt")
try:
    A.sandbox_open(ws, "k.txt")
    check("open refuses a file link", False)
except A.SandboxPathError:
    check("open refuses a file link", True)
try:
    A.sandbox_write(ws, "k.txt", b"overwrite", exclusive=False)
    check("non-exclusive write refuses a file link", False)
except A.SandboxPathError:
    check("non-exclusive write refuses a file link", True)
check("the linked target is untouched", (target / "secret.txt").read_text() == "host secret")
check("unlink removes the link itself, not its target",
      A.sandbox_unlink(ws, "k.txt") and (target / "secret.txt").exists())
check("walk lists no links", [r for r, _ in A.sandbox_walk(ws)] == [])

import shutil  # noqa: E402
shutil.rmtree(root)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
