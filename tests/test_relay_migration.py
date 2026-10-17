"""Migration must retain runner settings and leave unrelated services alone."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("relay", Path(__file__).resolve().parents[1] / "deploy/relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class MigrationTests(unittest.TestCase):
    def test_import_retains_runner_settings_and_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "runner"
            root.mkdir()
            source = Path(folder) / "source"
            source.mkdir()
            (root / "keys.env").write_text("REDIS_URL=redis://localhost:6381/0\nRELAY_SECRET=runner-secret\n")
            (root / "stellar_local.db").write_bytes(b"old database")
            (source / "keys.env").write_text("REDIS_URL=redis://localhost:6379/0\nRELAY_SECRET=front-secret\nDATABASE_NAME=/app/stellar_local.db\n")
            (source / "db").write_bytes(b"hosted database")
            with patch.object(relay, "ROOT", root), patch.object(relay, "stop_local_stellar") as stop, patch.dict(os.environ, {}, clear=True):
                relay.Mover().install(source)
                stop.assert_called_once()
                self.assertEqual(relay.keys_env()["REDIS_URL"], "redis://localhost:6381/0")
                self.assertEqual(relay.keys_env()["RELAY_SECRET"], "runner-secret")
                self.assertEqual(relay.keys_env()["DATABASE_NAME"], str(root / "stellar_local.db"))
            self.assertEqual((root / "stellar_local.db").read_bytes(), b"hosted database")
            backups = list((root / ".relay").glob("before-import-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "db").read_bytes(), b"old database")
            self.assertIn("runner-secret", (backups[0] / "keys.env").read_text())

    def test_stop_failure_does_not_replace_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (root / "keys.env").write_text("RELAY_SECRET=old\n")
            (source / "keys.env").write_text("RELAY_SECRET=new\n")
            with patch.object(relay, "ROOT", root), patch.object(relay, "stop_local_stellar", side_effect=RuntimeError("busy")):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    relay.Mover().install(source)
            self.assertEqual((root / "keys.env").read_text(), "RELAY_SECRET=old\n")

    def test_only_checkout_gunicorn_is_stopped(self):
        original_read = Path.read_bytes
        original_resolve = Path.resolve
        commands = {"10": b"gunicorn\0app:create_app()", "20": b"gunicorn\0app:create_app()", "30": b"redis-server\0"}

        def read(path):
            if str(path).startswith("/proc/"):
                return commands[path.parts[2]]
            return original_read(path)

        def resolve(path, *args, **kwargs):
            if str(path) == "/proc/10/cwd":
                return relay.ROOT.resolve()
            if str(path) == "/proc/20/cwd":
                return Path("/another-checkout")
            return original_resolve(path, *args, **kwargs)

        with patch.object(relay.os, "listdir", return_value=["10", "20", "30"]), patch.object(Path, "read_bytes", read), patch.object(Path, "resolve", resolve), patch.object(Path, "read_text", side_effect=FileNotFoundError), patch.object(relay.os, "kill") as kill:
            relay.stop_local_stellar()
        kill.assert_called_once_with(10, relay.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
