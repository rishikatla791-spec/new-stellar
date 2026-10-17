"""Migration must retain runner settings and leave unrelated services alone."""
import importlib.util
import os
import sqlite3
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
            with sqlite3.connect(root / 'stellar_local.db') as db:
                db.execute('CREATE TABLE records(value TEXT)')
                db.execute("INSERT INTO records VALUES('old database')")
            (source / "keys.env").write_text("REDIS_URL=redis://localhost:6379/0\nRELAY_SECRET=front-secret\nDATABASE_NAME=/app/stellar_local.db\n")
            with sqlite3.connect(source / 'db') as db:
                db.execute('CREATE TABLE records(value TEXT)')
                db.execute("INSERT INTO records VALUES('hosted database')")
            with patch.object(relay, "ROOT", root), patch.object(relay, "stop_local_stellar") as stop, patch.dict(os.environ, {}, clear=True):
                relay.Mover().install(source)
                stop.assert_called_once()
                self.assertEqual(relay.keys_env()["REDIS_URL"], "redis://localhost:6381/0")
                self.assertEqual(relay.keys_env()["RELAY_SECRET"], "runner-secret")
                self.assertEqual(relay.keys_env()["DATABASE_NAME"], str(root / "stellar_local.db"))
            with sqlite3.connect(root / 'stellar_local.db') as db:
                self.assertEqual(db.execute('SELECT value FROM records').fetchone()[0], 'hosted database')
            backups = list((root / ".relay").glob("before-import-*"))
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0] / 'db') as db:
                self.assertEqual(db.execute('SELECT value FROM records').fetchone()[0], 'old database')
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

    def test_invalid_database_does_not_change_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (root / 'keys.env').write_text('RELAY_SECRET=old\n')
            (source / 'db').write_bytes(b'not a database')
            with patch.object(relay, 'ROOT', root), patch.object(relay, 'stop_local_stellar') as stop:
                with self.assertRaises(sqlite3.DatabaseError):
                    relay.Mover().install(source)
                stop.assert_not_called()
            self.assertEqual((root / 'keys.env').read_text(), 'RELAY_SECRET=old\n')

    def test_backup_includes_committed_wal_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (root / 'keys.env').write_text('')
            db = sqlite3.connect(root / 'stellar_local.db')
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA wal_autocheckpoint=0')
                db.execute('CREATE TABLE records(value TEXT)')
                db.execute("INSERT INTO records VALUES('committed in WAL')")
                db.commit()
                self.assertTrue((root / 'stellar_local.db-wal').exists())
                with patch.object(relay, 'ROOT', root), patch.object(relay, 'stop_local_stellar'), patch.dict(os.environ, {}, clear=True):
                    relay.Mover().install(source)
                # No replacement source DB in this case; checkpoint the old DB
                # before any later move and verify its committed records survived.
                copied = root / 'backup.sqlite'
                import shutil
                shutil.copy2(root / 'stellar_local.db', copied)
                with sqlite3.connect(copied) as backup:
                    self.assertEqual(backup.execute('SELECT value FROM records').fetchone()[0], 'committed in WAL')
            finally:
                db.close()

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
