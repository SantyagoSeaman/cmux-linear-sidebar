import os
import subprocess
import unittest
from unittest import mock

from linear_sidebar import config
from linear_sidebar.config import KeychainError, SettingsError


def security(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(["/usr/bin/security"], returncode, stdout, stderr)


class KeychainTest(unittest.TestCase):
    """`security` is never run for real: its answers are canned."""

    def keychain_with(self, effect):
        with mock.patch.object(config.subprocess, "run", side_effect=[effect]):
            return config.keychain_password("cmux-linear-sidebar")

    def test_the_stored_key(self):
        self.assertEqual(self.keychain_with(security(0, "lin_api_abc\n")), "lin_api_abc")

    def test_a_missing_item_is_none(self):
        stderr = "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.\n"
        self.assertIsNone(self.keychain_with(security(44, stderr=stderr)))

    def test_a_locked_or_refusing_keychain_is_an_error(self):
        cases = {
            security(36, stderr="security: User interaction is not allowed.\n"): "User interaction is not allowed",
            security(128): "exit status 128",
            subprocess.TimeoutExpired(["/usr/bin/security"], 30): "did not answer",
            FileNotFoundError(2, "No such file or directory"): "No such file",
        }
        for effect, message in cases.items():
            with self.subTest(effect=effect):
                with self.assertRaisesRegex(KeychainError, message):
                    self.keychain_with(effect)


class ApiKeyTest(unittest.TestCase):
    def test_the_environment_wins_without_asking_the_keychain(self):
        with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "lin_api_env"}), \
                mock.patch.object(config, "keychain_password") as keychain:
            self.assertEqual(config.api_key(), ("lin_api_env", "environment"))
        keychain.assert_not_called()

    def test_keychain_then_nothing(self):
        with mock.patch.dict(os.environ, {"LINEAR_API_KEY": ""}):
            with mock.patch.object(config, "keychain_password", return_value="lin_api_kc"):
                self.assertEqual(config.api_key(), ("lin_api_kc", "Keychain"))
            with mock.patch.object(config, "keychain_password", return_value=None):
                self.assertEqual(config.api_key(), (None, None))


class LoadTest(unittest.TestCase):
    def test_options_win_over_the_environment(self):
        with mock.patch.dict(os.environ, {"LINEAR_SIDEBAR_PORT": "not a port", "LINEAR_SIDEBAR_REFRESH_SECONDS": "5"}):
            settings = config.load(port=47900, refresh_seconds=30)
        self.assertEqual((settings.port, settings.refresh_seconds), (47900, 30))

    def test_environment_and_defaults(self):
        with mock.patch.dict(os.environ, {"LINEAR_SIDEBAR_PORT": "47802", "LINEAR_SIDEBAR_REFRESH_SECONDS": ""}):
            settings = config.load()
        self.assertEqual((settings.port, settings.refresh_seconds), (47802, config.DEFAULT_REFRESH_SECONDS))

    def test_bad_values(self):
        for port in ("0", "65536", "http", "-1"):
            with self.subTest(port=port):
                with mock.patch.dict(os.environ, {"LINEAR_SIDEBAR_PORT": port}):
                    with self.assertRaises(SettingsError):
                        config.load()


if __name__ == "__main__":
    unittest.main()
