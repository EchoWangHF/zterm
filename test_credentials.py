"""Keyring and askpass tests; no real account credentials are accessed."""

import os
import unittest
from unittest.mock import Mock, patch

import credentials


class CredentialTests(unittest.TestCase):
    def test_password_goes_only_to_keyring_and_not_ssh_environment(self):
        profile = {"host": "192.0.2.10", "username": "zhouyawei", "password_saved": True}
        with patch.object(credentials.Secret, "password_store_sync", return_value=True) as store:
            credentials.save_password("server-one", "开发服务器", "test-only-password")
            self.assertEqual(store.call_args.args[1], {"server": "server-one"})
            self.assertEqual(store.call_args.args[-2], "test-only-password")
        environment = credentials.ssh_environment("server-one", profile)
        updated = credentials.ssh_environment("server-one", dict(profile, credential_id="password-new"))
        self.assertEqual(updated["ZTERM_SERVER_ID"], "password-new")
        self.assertEqual(environment["ZTERM_SERVER_ID"], "server-one")
        self.assertNotIn("test-only-password", str(environment))
        self.assertEqual(environment["SSH_ASKPASS_REQUIRE"], "force")
        self.assertTrue(os.access(environment["SSH_ASKPASS"], os.X_OK))
        self.assertEqual(credentials.ssh_environment("server-one", dict(profile, password_saved=False)), {})

    def test_askpass_supplies_password_only_for_matching_target(self):
        environment = {"ZTERM_SERVER_ID": "server-one", "ZTERM_SSH_USER": "zhouyawei",
                       "ZTERM_SSH_HOST": "192.0.2.10", "ZTERM_SSH_BACKGROUND": "1"}
        with patch.dict(os.environ, environment), \
                patch("credentials.lookup_password", return_value="test-only-password") as lookup:
            self.assertEqual(credentials.askpass("zhouyawei@192.0.2.10's password: "), "test-only-password")
            lookup.assert_called_once_with("server-one", True)
            lookup.reset_mock()
            for prompt in ("root@192.0.2.10's password:", "zhouyawei@192.0.2.11's password:",
                           "Enter passphrase for key '/tmp/example':", "Are you sure you want to continue connecting?"):
                self.assertIsNone(credentials.askpass(prompt))
            lookup.assert_not_called()

    def test_background_lookup_never_unlocks_or_prompts(self):
        service = Mock()
        locked = Mock()
        locked.get_secret.return_value = None
        unlocked = Mock()
        unlocked.get_secret.return_value.get_text.return_value = "test-only-password"
        service.search_sync.return_value = [locked, unlocked]
        with patch.object(credentials.Secret.Service, "get_sync", return_value=service):
            self.assertEqual(credentials.lookup_password("server-one", background=True), "test-only-password")
            flags = service.search_sync.call_args.args[2]
            self.assertTrue(flags & credentials.Secret.SearchFlags.LOAD_SECRETS)
            self.assertFalse(flags & credentials.Secret.SearchFlags.UNLOCK)
            service.search_sync.return_value = [locked]
            self.assertIsNone(credentials.lookup_password("server-one", background=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
