"""Download Stockfish, so chess is played at a strength you choose.

    .venv/Scripts/python.exe setup_engine.py

Optional. Without it the built-in Python search in chess_engine.py handles
chess at roughly 1700-1900, which is a decent club opponent. With it you get
UCI_Elo: strength stops being an estimate and becomes a number between 1320
and 3190 that you set.

The binary is around 100MB, so it is downloaded here rather than committed -
GitHub refuses files over 100MB, and a repository should not carry a
platform-specific executable anyway. engines/ is gitignored.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ENGINES = PROJECT_ROOT / "engines"
RELEASES = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"


def _asset_for_this_machine(assets: list[dict]) -> dict | None:
    """Pick the right build for this OS and CPU."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")

    if system == "windows":
        want = "windows-arm64" if arm else "windows-x86-64"
    elif system == "darwin":
        want = "macos-m1-apple-silicon" if arm else "macos-x86-64"
    else:
        want = "ubuntu-x86-64"

    # Prefer the portable "universal" build: the avx2/bmi2 variants are
    # faster but crash outright on a CPU without those instructions, which
    # is a miserable way to find out your machine is older than you thought.
    matches = [a for a in assets if want in a["name"].lower()]
    universal = [a for a in matches if "universal" in a["name"].lower()]
    return (universal or matches or [None])[0]


def main() -> int:
    print("\n  Stockfish setup\n")

    existing = list(ENGINES.rglob("stockfish*")) if ENGINES.exists() else []
    existing = [p for p in existing if p.is_file()]
    if existing:
        print(f"  already present: {existing[0].name} "
              f"({existing[0].stat().st_size / 1e6:.0f} MB)")
        print("  delete engines/ to re-download\n")
        return 0

    if shutil.which("stockfish"):
        print("  found a system-wide stockfish on PATH; nothing to download\n")
        return 0

    try:
        req = urllib.request.Request(RELEASES, headers={"User-Agent": "stellar"})
        release = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as exc:
        print(f"  Could not reach the GitHub releases API: {exc}")
        print("  Chess will fall back to the built-in Python engine.\n")
        return 1

    asset = _asset_for_this_machine(release.get("assets", []))
    if not asset:
        print(f"  No build published for {platform.system()} {platform.machine()}.")
        print("  Chess will use the built-in Python engine.\n")
        return 1

    ENGINES.mkdir(exist_ok=True)
    archive = ENGINES / asset["name"]

    print(f"  release  : {release['tag_name']}")
    print(f"  asset    : {asset['name']}")
    print(f"  size     : {asset['size'] / 1e6:.1f} MB")
    print("  downloading ...")

    try:
        urllib.request.urlretrieve(asset["browser_download_url"], archive)
    except Exception as exc:
        print(f"  Download failed: {exc}\n")
        return 1

    print("  extracting ...")
    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(ENGINES)
    except Exception as exc:
        print(f"  Could not extract: {exc}\n")
        return 1
    finally:
        archive.unlink(missing_ok=True)

    binaries = [p for p in ENGINES.rglob("stockfish*") if p.is_file()]
    if not binaries:
        print("  Extracted, but no stockfish binary was found inside.\n")
        return 1

    binary = max(binaries, key=lambda p: p.stat().st_size)
    if sys.platform != "win32":
        binary.chmod(0o755)

    print(f"  installed: {binary.relative_to(PROJECT_ROOT)} "
          f"({binary.stat().st_size / 1e6:.0f} MB)")

    # Prove it actually runs before claiming success - a downloaded file is
    # not the same as a working engine.
    try:
        import chess
        import chess.engine
        with chess.engine.SimpleEngine.popen_uci(str(binary)) as eng:
            name = eng.id.get("name", "?")
            elo = eng.options.get("UCI_Elo")
            print(f"  verified : {name}")
            if elo:
                print(f"  strength : settable from {elo.min} to {elo.max} elo")
    except Exception as exc:
        print(f"  Downloaded, but it will not run: {exc}")
        return 1

    print("\n  Done. Chess now plays at the elo you ask for.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
