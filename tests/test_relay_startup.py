"""Hosting platforms must preserve an explicitly configured relay on restart."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class RelayStartupTests(unittest.TestCase):
    def check(self, platform, config, expected):
        with patch.object(app.os, 'name', platform), patch.object(app, 'env', side_effect=lambda key: config.get(key, '')), patch.object(app.os, 'execv') as execute:
            app._start_configured_relay()
        if expected:
            execute.assert_called_once_with(sys.executable, [sys.executable, str(app.PROJECT_ROOT / 'deploy' / 'auto_pull.py')])
        else:
            execute.assert_not_called()

    def test_configured_linux_front_uses_supervisor(self):
        self.check('posix', {'RELAY_SECRET': 'configured'}, True)

    def test_runner_does_not_start_front(self):
        self.check('posix', {'RELAY_SECRET': 'configured', 'STELLAR_RELAY_ROLE': 'runner'}, False)

    def test_unconfigured_development_server_unchanged(self):
        self.check('posix', {}, False)

    def test_managed_host_remains_supervised_without_laptop_relay(self):
        self.check('posix', {'STELLAR_MANAGED_START': '1'}, True)

    def test_windows_does_not_start_linux_supervisor(self):
        self.check('nt', {'RELAY_SECRET': 'configured'}, False)
