"""Model tests and real VTE lifecycle checks, run under xvfb-run."""

import os
import json
from pathlib import Path
import tempfile
import time
import unittest

from model import (AliasStore, AppearanceStore, THEMES, default_appearance, interface_colors,
                   SSH_KEEPALIVE, bash_ssh_aliases, ssh_command, server_ssh_command,
                   validate_server, valid_alias)


def pump_until(predicate, timeout=5):
    from app import Gtk

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("GTK callback did not complete before timeout")


class ModelTests(unittest.TestCase):
    def test_server_profiles_roundtrip_and_validation(self):
        profile = {"name": "开发服务器 A", "host": "192.0.2.10", "username": "zhouyawei",
                   "port": 22, "password_saved": True}
        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["old-alias", "server-one"], {"server-one": profile})
            restored = AliasStore(store.path)
            self.assertEqual(restored.load(), ["old-alias", "server-one"])
            self.assertEqual(restored.profiles, {"server-one": profile})
            before = store.path.read_bytes()
            for invalid in ({"host": "hostname"}, {"host": "999.1.1.1"}, {"port": 0},
                            {"port": 65536}, {"port": True}, {"username": "-root"},
                            {"username": "user;command"}, {"name": "\n"}, {"name": "a" * 81},
                            {"password": "must-not-be-written"}):
                with self.assertRaises(ValueError):
                    store.save(["old-alias", "server-one"], {"server-one": dict(profile, **invalid)})
                self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(validate_server(dict(profile, host="2001:db8::1"))["host"], "2001:db8::1")
            restored.save(["old-alias"])
            self.assertEqual(json.loads(store.path.read_text()), {"aliases": ["old-alias"]})

    def test_explicit_server_user_ip_and_port_override_ssh_config(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config"
            config.write_text("Host *\n User cambricon\n HostName 192.0.2.99\n Port 2200\n"
                              " IdentityFile /tmp/example-key\n BatchMode yes\n")
            for host, port in (("192.0.2.10", 22), ("2001:db8::1", 2222)):
                profile = {"name": "仅作显示", "host": host, "username": "zhouyawei", "port": port,
                           "password_saved": True}
                command = server_ssh_command(profile)
                result = subprocess.run(command[:1] + ["-G", "-F", str(config)] + command[1:],
                                        capture_output=True, text=True, check=True)
                options = dict(line.split(" ", 1) for line in result.stdout.splitlines())
                self.assertEqual(options["user"], "zhouyawei")
                self.assertEqual(options["hostname"], host)
                self.assertEqual(options["port"], str(port))
                self.assertEqual(options["identityfile"], "/tmp/example-key")
                self.assertEqual(options["batchmode"], "no")
                self.assertEqual(options["preferredauthentications"], "publickey,password")
                self.assertNotIn(profile["name"], command)

    def test_gnome_presets_match_source_colors(self):
        expected = {
            "gnome-black-on-light-yellow": ("#000000", "#ffffdd"),
            "gnome-black-on-white": ("#000000", "#ffffff"),
            "gnome-gray-on-black": ("#aaaaaa", "#000000"),
            "gnome-white-on-black": ("#ffffff", "#000000"),
            "gnome-green-on-black": ("#00ff00", "#000000"),
            "ubuntu-default": ("#ffffff", "#300a24"),
        }
        self.assertEqual(len(THEMES), 14)
        self.assertTrue({"classic", "dark", "light", "ocean"}.isdisjoint(THEMES))
        for name, (fg, bg) in expected.items():
            self.assertEqual((THEMES[name]["foreground"], THEMES[name]["background"]), (fg, bg))
            self.assertEqual(THEMES[name]["cursor"], fg)
        self.assertEqual(default_appearance()["theme"], "ubuntu-default")

    def test_removed_themes_migrate_without_overwriting_custom_colors(self):
        old_presets = {
            "classic": ("ubuntu-default", "#eeeeec", "#241f31", "#eeeeec"),
            "dark": ("gnome-gray-on-black", "#e5e7eb", "#15181e", "#93c5fd"),
            "light": ("gnome-black-on-white", "#24292f", "#fafafa", "#0969da"),
            "ocean": ("ubuntu-default", "#d8e7f3", "#102332", "#7dd3fc"),
        }
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(Path(directory) / "appearance.json")
            for old, (new, fg, bg, cursor) in old_presets.items():
                settings = {"theme": old, "font": "Monospace Bold 15",
                            "foreground": fg, "background": bg, "cursor": cursor}
                before = json.dumps(settings)
                store.path.write_text(before)
                migrated = store.load()
                self.assertEqual(migrated["theme"], new)
                self.assertEqual(migrated["font"], settings["font"])
                for key in ("foreground", "background", "cursor"):
                    self.assertEqual(migrated[key], THEMES[new][key])
                self.assertEqual(store.path.read_text(), before, "Reading must not rewrite configuration")
                custom = dict(settings, foreground="#123456", background="#654321", cursor="#ABCDEF")
                store.save(custom)
                self.assertEqual(custom["theme"], old, "Saving must not mutate the caller's settings")
                self.assertEqual(store.load(), dict(custom, theme=new, interface_theme="follow-terminal"))

    def test_appearance_roundtrip_is_independent_of_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            aliases = AliasStore(Path(directory) / "servers.json")
            aliases.save(["dev-server"])
            store = AppearanceStore(Path(directory) / "appearance.json")
            self.assertEqual(store.load(), default_appearance())
            settings = dict(default_appearance(), font="Monospace Bold 15", background="#123456")
            store.save(settings)
            self.assertEqual(AppearanceStore(store.path).load(), settings)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(aliases.load(), ["dev-server"])

    def test_invalid_appearance_does_not_overwrite_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(Path(directory) / "appearance.json")
            store.save(default_appearance())
            before = store.path.read_bytes()
            for changes in ({"font": ""}, {"theme": "unknown"}, {"theme": []},
                            {"interface_theme": "unknown"}, {"interface_theme": []},
                            {"foreground": "not-a-color"}, {"background": None}):
                with self.assertRaises(ValueError):
                    store.save(dict(default_appearance(), **changes))
                self.assertEqual(store.path.read_bytes(), before)
            store.path.write_text('{"font": 23}')
            with self.assertRaises(ValueError):
                store.load()
            self.assertEqual(store.path.read_text(), '{"font": 23}')

    def test_saved_aliases_roundtrip_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "settings/servers.json")
            self.assertEqual(store.load(), [])
            store.save(["dev-one", "prod_two"])
            self.assertEqual(store.load(), ["dev-one", "prod_two"])
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            store.save(["prod_two"])
            self.assertEqual(store.load(), ["prod_two"])

    def test_invalid_saved_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "servers.json"
            path.write_text('{"aliases": ["-oProxyCommand=bad"]}')
            with self.assertRaises(ValueError):
                AliasStore(path).load()
            self.assertIn("ProxyCommand", path.read_text())

    def test_bashrc_login_user_overrides_ssh_config_for_alias_and_ip(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bashrc = root / "bashrc"
            bashrc.write_text("alias lab='ssh -o serveraliveinterval=60 zhouyawei@192.0.2.24'\n"
                              "alias lab2='ssh -l zhouyawei -p2222 192.0.2.25'\n")
            config = root / "config"
            config.write_text("Host *\n User cambricon\n IdentityFile /tmp/example-key\n")
            self.assertEqual(list(bash_ssh_aliases(bashrc)), ["lab", "lab2"])
            for alias in ("lab", "192.0.2.24"):
                command = ssh_command(alias, bashrc)
                result = subprocess.run(command[:1] + ["-G", "-F", str(config)] + command[1:],
                                        capture_output=True, text=True, check=True)
                values = dict(line.split(" ", 1) for line in result.stdout.splitlines())
                self.assertEqual(values["user"], "zhouyawei")
                self.assertEqual(values["hostname"], "192.0.2.24")
                self.assertEqual(values["serveraliveinterval"], "60")
                self.assertIn("/tmp/example-key", result.stdout)
            self.assertIn("2222", ssh_command("lab2", bashrc))
            self.assertEqual(ssh_command("other", bashrc), ["/usr/bin/ssh", *SSH_KEEPALIVE, "other"])

    def test_bashrc_aliases_are_static_and_never_execute_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bashrc = root / "bashrc"
            marker = root / "should-not-exist"
            bashrc.write_text(f"touch {marker}\n"
                              f"alias evil='ssh user@host; touch {marker}'\n"
                              f"alias substitution='ssh $(touch {marker})@host'\n"
                              "alias command='ssh user@host reboot'\n"
                              "alias unsafe='ssh -oProxyCommand=evil user@host'\n"
                              "alias previous='ssh old@host'\nalias previous='echo replaced'\n"
                              "alias good='ssh -o User=zhouyawei -o HostName=192.0.2.10 host'\n")
            self.assertEqual(list(bash_ssh_aliases(bashrc)), ["good"])
            self.assertEqual(ssh_command("good", bashrc),
                             ["/usr/bin/ssh", "-l", "zhouyawei", "-o", "HostName=192.0.2.10",
                              *SSH_KEEPALIVE, "good"])
            self.assertFalse(marker.exists())

    def test_arguments_cannot_be_ssh_options_or_shell_commands(self):
        for alias in ["-F", "host;touch /tmp/x", "a b", "$(id)", "", "host\nname"]:
            self.assertFalse(valid_alias(alias))
            with self.assertRaises(ValueError):
                ssh_command(alias)
        self.assertEqual(ssh_command("dev-one"), ["/usr/bin/ssh", *SSH_KEEPALIVE, "dev-one"])

    def test_ssh_commands_send_keepalive_every_60s(self):
        profile = {"name": "开发服务器", "host": "192.0.2.10", "username": "zhouyawei",
                   "port": 22, "password_saved": False}
        keepalive = ["-o", "ServerAliveInterval=60", "-o", "ServerAliveCountMax=3"]

        def contains(command):
            return any(command[i:i + len(keepalive)] == keepalive
                       for i in range(len(command) - len(keepalive) + 1))

        # Saved-server and plain-alias paths both keep the connection alive.
        self.assertTrue(contains(server_ssh_command(profile)))
        self.assertTrue(contains(server_ssh_command(dict(profile, password_saved=True))))
        self.assertTrue(contains(ssh_command("dev-server")))

    def test_bashrc_alias_keepalive_overrides_default(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            bashrc = Path(directory) / "bashrc"
            bashrc.write_text("alias lab='ssh -o ServerAliveInterval=30 zhouyawei@192.0.2.24'\n")
            config = Path(directory) / "config"
            config.write_text("")
            command = ssh_command("lab", bashrc)
            result = subprocess.run(command[:1] + ["-G", "-F", str(config)] + command[1:],
                                    capture_output=True, text=True, check=True)
            options = dict(line.split(" ", 1) for line in result.stdout.splitlines())
            self.assertEqual(options["serveraliveinterval"], "30")
            self.assertEqual(options["serveralivecountmax"], "3")


class TerminalTests(unittest.TestCase):
    def test_server_context_edit_preserves_running_sessions_and_order(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            profile = {"name": "原服务器", "host": "192.0.2.10", "username": "old-user",
                       "port": 22, "password_saved": False}
            store.save(["server-a", "server-b"], {"server-a": profile})
            window = Window(store=store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
            try:
                pump_until(lambda: window.rows["server-a"].get_allocated_height() > 1)
                row = window.rows["server-a"]
                event = SimpleNamespace(button=3, y=row.get_allocation().y + 4)
                menu = Mock()
                with patch.object(window, "server_context_menu", return_value=menu) as create:
                    self.assertTrue(window.server_drag_pressed(window.server_list, event))
                    create.assert_called_once_with("server-a")
                    menu.popup_at_pointer.assert_called_once_with(event)
                self.assertIsNone(window.current)
                self.assertEqual(window.groups, {})
                menu = window.server_context_menu("server-a")
                self.assertEqual(menu.get_children()[0].get_label(), "编辑服务器")
                menu.get_children()[0].activate()
                menu.destroy()
                dialog = window.server_editors["server-a"]
                self.assertIs(window.edit_dialog("server-a"), dialog)
                self.assertEqual(dialog.username.get_text(), "old-user")
                self.assertEqual(dialog.password.get_text(), "")
                self.assertFalse(dialog.get_modal())
                self.assertIsNone(dialog.get_transient_for())
                window.server_list.select_row(row)
                pump_until(lambda: window.current_session().alive)
                window.new_session()
                pump_until(lambda: window.current_session().alive)
                sessions = list(window.groups["server-a"].get_children())
                pids = [session.pid for session in sessions]
                window.command_factory = window.default_command
                dialog.name.set_text("新别名")
                dialog.host.set_text("2001:db8::2")
                dialog.username.set_text("zhouyawei")
                dialog.port.set_value(2222)
                dialog.response(Gtk.ResponseType.OK)
                pump_until(lambda: not dialog.busy)
                self.assertTrue(dialog.closed)
                self.assertEqual(window.aliases, ["server-a", "server-b"])
                self.assertEqual(store.load(), window.aliases)
                self.assertIs(window.rows["server-a"], row)
                self.assertEqual(row.get_child().get_text(), "新别名")
                self.assertEqual(window.header.get_title(), "新别名")
                self.assertEqual(window.connection_label.get_text(), "新别名 · 在线")
                self.assertEqual([session.pid for session in sessions], pids)
                self.assertTrue(all(session.alive for session in sessions))
                expected = server_ssh_command(window.profiles["server-a"])
                self.assertTrue(all(session.argv == expected for session in sessions))
                self.assertEqual(window.default_command("server-a"), expected)
                window.search.set_text("2001:db8::2")
                window.server_list.invalidate_filter()
                self.assertTrue(row.get_child_visible())
                self.assertFalse(window.rows["server-b"].get_child_visible())
            finally:
                window.destroy()

    def test_sidebar_has_persistent_add_edit_duplicate_buttons(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["server-a"], {"server-a": {"name": "开发服务器", "host": "192.0.2.10",
                                                    "username": "zhouyawei", "port": 22,
                                                    "password_saved": False}})
            window = Window(store=store, enable_quota=False,
                            command_factory=lambda _: ["/bin/cat"])
            try:
                # 侧栏顶部的“添加服务器”始终可见，且不再出现在“设置”菜单中。
                pump_until(lambda: window.add_server_button.get_mapped())
                self.assertTrue(window.add_server_button.get_visible())
                self.assertNotIn("添加服务器", [item.get_label()
                                              for item in window.settings_menu.get_children()])
                with patch.object(window, "add_dialog") as add:
                    window.add_server_button.clicked()
                    add.assert_called_once_with()
                # 未选中服务器时，编辑/复制/移除都不可用。
                self.assertFalse(window.edit_button.get_sensitive())
                self.assertFalse(window.duplicate_button.get_sensitive())
                self.assertFalse(window.remove_button.get_sensitive())
                # 选中服务器后点亮，点击分别触发对应动作。
                window.server_list.select_row(window.rows["server-a"])
                self.assertTrue(window.edit_button.get_sensitive())
                self.assertTrue(window.duplicate_button.get_sensitive())
                self.assertTrue(window.remove_button.get_sensitive())
                with patch.object(window, "edit_dialog") as edit:
                    window.edit_button.clicked()
                    edit.assert_called_once_with("server-a")
                # 本地终端行没有可编辑/复制的名字。
                window.server_list.select_row(window.rows[""])
                self.assertFalse(window.edit_button.get_sensitive())
                self.assertFalse(window.duplicate_button.get_sensitive())
                self.assertFalse(window.remove_button.get_sensitive())
            finally:
                window.destroy()

    def test_duplicate_server_copies_saved_password(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["server-a"], {"server-a": {"name": "原服务器", "host": "192.0.2.10",
                                                    "username": "zhouyawei", "port": 22,
                                                    "password_saved": True}})
            window = Window(store=store, enable_quota=False,
                            command_factory=lambda _: ["/bin/cat"])
            try:
                with patch("credentials.lookup_password", return_value="test-only-password") as lookup, \
                     patch("credentials.save_password") as save:
                    window.duplicate_alias("server-a")
                new_id = [alias for alias in window.aliases if alias != "server-a"][0]
                self.assertEqual(window.aliases, ["server-a", new_id])
                self.assertEqual(store.load(), window.aliases)
                copy = window.profiles[new_id]
                self.assertEqual(copy["name"], "原服务器 副本")
                self.assertEqual(copy["host"], "192.0.2.10")
                self.assertEqual(copy["username"], "zhouyawei")
                self.assertEqual(copy["port"], 22)
                self.assertTrue(copy["password_saved"])
                self.assertEqual(copy["credential_id"], new_id)
                self.assertIn(new_id, window.rows)
                self.assertEqual(window.rows[new_id].alias, new_id)
                lookup.assert_called_once_with("server-a")
                save.assert_called_once_with(new_id, "原服务器 副本", "test-only-password")
            finally:
                window.destroy()

    def test_duplicate_server_without_password_keeps_names_unique(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["server-a"], {"server-a": {"name": "原服务器", "host": "192.0.2.10",
                                                    "username": "zhouyawei", "port": 22,
                                                    "password_saved": False}})
            window = Window(store=store, enable_quota=False,
                            command_factory=lambda _: ["/bin/cat"])
            try:
                with patch("credentials.save_password") as save:
                    window.duplicate_alias("server-a")
                    window.duplicate_alias("server-a")
                copies = [alias for alias in window.aliases if alias != "server-a"]
                self.assertEqual(len(copies), 2)
                self.assertEqual([window.profiles[alias]["name"] for alias in copies],
                                 ["原服务器 副本", "原服务器 副本 2"])
                for alias in copies:
                    self.assertFalse(window.profiles[alias]["password_saved"])
                    self.assertNotIn("credential_id", window.profiles[alias])
                save.assert_not_called()
            finally:
                window.destroy()

    def test_edit_password_keep_replace_clear_and_rollback(self):
        from unittest.mock import patch
        from app import Window, Gtk

        for old_key in ("server-a", "password-existing"):
            with self.subTest(old_key=old_key), tempfile.TemporaryDirectory() as directory:
                store = AliasStore(Path(directory) / "servers.json")
                profile = {"name": "开发服务器", "host": "192.0.2.10", "username": "zhouyawei",
                           "port": 22, "password_saved": True}
                if old_key != "server-a":
                    profile["credential_id"] = old_key
                store.save(["server-a"], {"server-a": profile})
                window = Window(store=store, enable_quota=False)
                try:
                    with patch("app.credentials.save_password") as save, \
                            patch("app.credentials.delete_password") as delete:
                        dialog = window.edit_dialog("server-a")
                        self.assertTrue(dialog.clear_password.get_visible())
                        dialog.response(Gtk.ResponseType.OK)
                        pump_until(lambda: not dialog.busy)
                        self.assertTrue(dialog.closed)
                        self.assertEqual(window.profiles["server-a"], profile)
                        save.assert_not_called()
                        delete.assert_not_called()
                        dialog = window.edit_dialog("server-a")
                        dialog.password.set_text("new-test-password")
                        before = store.path.read_bytes()
                        with patch.object(store, "save", side_effect=OSError("read only")):
                            dialog.response(Gtk.ResponseType.OK)
                            pump_until(lambda: not dialog.busy)
                        staged_key = save.call_args.args[0]
                        self.assertNotEqual(staged_key, old_key)
                        delete.assert_called_once_with(staged_key)
                        self.assertEqual(store.path.read_bytes(), before)
                        self.assertEqual(window.profiles["server-a"], profile)
                        save.reset_mock()
                        delete.reset_mock()

                        def cleanup(key):
                            # Old secret is removed only after the new reference is durable.
                            self.assertEqual(key, old_key)
                            stored = json.loads(store.path.read_text())["servers"]["server-a"]
                            self.assertEqual(stored["credential_id"], save.call_args.args[0])
                        delete.side_effect = cleanup
                        dialog.response(Gtk.ResponseType.OK)
                        pump_until(lambda: not dialog.busy)
                        self.assertTrue(dialog.closed)
                        replacement = save.call_args.args[0]
                        self.assertEqual(window.profiles["server-a"]["credential_id"], replacement)
                        delete.assert_called_once_with(old_key)
                        self.assertNotIn("new-test-password", store.path.read_text())
                        save.reset_mock()
                        delete.reset_mock()
                        delete.side_effect = None
                        dialog = window.edit_dialog("server-a")
                        dialog.clear_password.set_active(True)
                        self.assertFalse(dialog.password.get_sensitive())
                        dialog.response(Gtk.ResponseType.OK)
                        pump_until(lambda: not dialog.busy)
                        self.assertFalse(window.profiles["server-a"]["password_saved"])
                        self.assertNotIn("credential_id", window.profiles["server-a"])
                        save.assert_not_called()
                        delete.assert_called_once_with(replacement)
                finally:
                    window.destroy()

    def test_edit_password_keyring_failure_and_removal_use_current_reference(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            profile = {"name": "服务器", "host": "192.0.2.10", "username": "zhouyawei", "port": 22,
                       "password_saved": True, "credential_id": "password-existing"}
            store.save(["server-a"], {"server-a": profile})
            window = Window(store=store, enable_quota=False)
            try:
                dialog = window.edit_dialog("server-a")
                dialog.password.set_text("test-only-password")
                with patch("app.credentials.save_password", side_effect=RuntimeError("locked")), \
                        patch("app.credentials.delete_password") as delete:
                    dialog.response(Gtk.ResponseType.OK)
                    pump_until(lambda: not dialog.busy)
                    self.assertFalse(dialog.closed)
                    self.assertEqual(window.profiles["server-a"], profile)
                    delete.assert_not_called()
                    window.current = "server-a"
                    window.remove_alias()
                    pump_until(lambda: delete.called)
                    delete.assert_called_once_with("password-existing")
                    dialog.response(Gtk.ResponseType.OK)
                    self.assertIn("已被移除", dialog.error_label.get_text())
                    self.assertEqual(store.load(), [])
            finally:
                window.destroy()

    def test_edit_during_async_save_does_not_resurrect_or_overwrite(self):
        import threading
        from unittest.mock import patch
        from app import Window, Gtk

        for action in ("remove", "close", "duplicate"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                store = AliasStore(Path(directory) / "servers.json")
                profile = {"name": "原服务器", "host": "192.0.2.10", "username": "zhouyawei",
                           "port": 22, "password_saved": False}
                store.save(["server-a"], {"server-a": profile})
                window = Window(store=store, enable_quota=False)
                dialog = window.edit_dialog("server-a")
                dialog.name.set_text("新别名")
                dialog.password.set_text("test-only-password")
                started, proceed = threading.Event(), threading.Event()

                def save_password(*_):
                    started.set()
                    if not proceed.wait(5):
                        raise RuntimeError("worker timeout")
                try:
                    with patch("app.credentials.save_password", side_effect=save_password) as save, \
                            patch("app.credentials.delete_password") as delete:
                        dialog.response(Gtk.ResponseType.OK)
                        pump_until(started.is_set)
                        if action == "close":
                            window.destroy()
                        elif action == "remove":
                            window.current = "server-a"
                            window.remove_alias()
                        else:
                            window.aliases.append("server-b")
                            window.profiles["server-b"] = dict(profile, name="新别名")
                            store.save(window.aliases, window.profiles)
                            window.add_row("server-b")
                        proceed.set()
                        pump_until(lambda: not dialog.busy)
                        delete.assert_called_once_with(save.call_args.args[0])
                        self.assertEqual(store.load(), [] if action == "remove" else
                                         ["server-a", "server-b"] if action == "duplicate" else ["server-a"])
                        if action != "remove":
                            self.assertEqual(store.profiles["server-a"], profile)
                finally:
                    proceed.set()
                    window.destroy()

    def test_legacy_editor_prefills_effective_ssh_options(self):
        from unittest.mock import patch
        from app import Window, Gtk
        from model import legacy_server_profile

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "ssh_config"
            config.write_text("Host old-server\n HostName 192.0.2.10\n User cambricon\n Port 2222\n")
            command = ["/usr/bin/ssh", "-F", str(config), "-l", "zhouyawei", "old-server"]
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["old-server"])
            window = Window(store=store, enable_quota=False)
            try:
                with patch("model.ssh_command", return_value=command):
                    profile = legacy_server_profile("old-server")
                    self.assertEqual(profile["username"], "zhouyawei")
                    dialog = window.edit_dialog("old-server")
                    pump_until(lambda: not dialog.loading)
                self.assertEqual(dialog.host.get_text(), "192.0.2.10")
                self.assertEqual(dialog.port.get_value_as_int(), 2222)
                self.assertEqual(dialog.username.get_text(), "zhouyawei")
                dialog.response(Gtk.ResponseType.OK)
                pump_until(lambda: not dialog.busy)
                self.assertEqual(store.load(), ["old-server"])
                self.assertEqual(store.profiles["old-server"], profile)
            finally:
                window.destroy()

    def test_quota_result_for_old_profile_is_discarded_after_edit(self):
        from unittest.mock import patch
        from app import Window

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            old = {"name": "服务器", "host": "192.0.2.10", "username": "old-user", "port": 22}
            store.save(["server-a"], {"server-a": old})
            window = Window(store=store, enable_quota=False)
            try:
                window.profiles["server-a"] = dict(old, username="new-user")
                window.quota_busy = True
                with patch.object(window, "refresh_quota") as refresh:
                    window.quota_received("server-a", {"status": "old account"}, old)
                    self.assertNotIn("server-a", window.quota_cache)
                    self.assertFalse(window.quota_busy)
                    refresh.assert_called_once_with()
            finally:
                window.destroy()

    def test_server_drag_order_persists_and_preserves_sessions(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import Window

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            profiles = {
                "server-a": {"name": "组 A", "host": "192.0.2.10", "username": "test-user", "port": 22},
                "server-b": {"name": "隐藏节点", "host": "192.0.2.11", "username": "test-user", "port": 2222},
                "server-c": {"name": "组 C", "host": "192.0.2.12", "username": "test-user", "port": 22},
            }
            store.save(list(profiles), profiles)
            window = Window(store=store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
            window.confirm = lambda *_: True
            reopened = None
            try:
                window.server_list.select_row(window.rows["server-c"])
                c_session = window.current_session()
                window.server_list.select_row(window.rows["server-a"])
                first = window.current_session()
                second = window.new_session()
                sessions = [c_session, first, second]
                pump_until(lambda: all(session.alive for session in sessions)
                           and window.rows["server-a"].get_allocated_height() > 1)
                pids = [session.pid for session in sessions]

                def drop(source, target, after=False):
                    row = window.rows[target]
                    data = SimpleNamespace(get_data=lambda: source.encode("ascii"))
                    with patch("app.Gtk.drag_finish") as finish:
                        window.server_dropped(row, object(), 0,
                                              row.get_allocated_height() if after else 0, data, 0, 0)
                        self.assertTrue(finish.call_args.args[1])

                def assert_order(expected):
                    self.assertEqual(window.aliases, expected)
                    self.assertEqual([row.alias for row in window.server_list.get_children()], [""] + expected)
                    self.assertEqual(store.load(), expected)
                    self.assertEqual(store.profiles, profiles)
                    self.assertEqual(window.current, "server-a")
                    self.assertIs(window.server_list.get_selected_row(), window.rows["server-a"])
                    self.assertIs(window.current_session(), second)
                    self.assertEqual([session.pid for session in sessions], pids)
                    self.assertTrue(all(session.alive for session in sessions))

                drop("server-c", "server-a")
                assert_order(["server-c", "server-a", "server-b"])
                drop("server-a", "server-b", after=True)
                assert_order(["server-c", "server-b", "server-a"])
                window.search.set_text("组")
                window.server_list.invalidate_filter()
                self.assertFalse(window.rows["server-b"].get_child_visible())
                drop("server-c", "server-a", after=True)
                assert_order(["server-b", "server-a", "server-c"])
                self.assertFalse(window.rows["server-b"].get_child_visible())
                window.search.set_text("")
                window.server_list.invalidate_filter()
                reopened = Window(store=AliasStore(store.path), enable_quota=False)
                self.assertEqual([row.alias for row in reopened.server_list.get_children()],
                                 ["", "server-b", "server-a", "server-c"])
                self.assertEqual(reopened.groups, {})
                before = store.path.read_bytes()
                with patch.object(store, "save", side_effect=OSError("read only")), \
                        patch.object(window, "info") as info:
                    self.assertFalse(window.reorder_server("server-a", "server-b", False))
                    info.assert_called_once()
                self.assertEqual(store.path.read_bytes(), before)
                assert_order(["server-b", "server-a", "server-c"])
                for source, target in (("", "server-a"), ("server-a", ""), ("removed-server", "server-a")):
                    self.assertFalse(window.reorder_server(source, target, False))
                self.assertTrue(window.reorder_server("server-a", "server-a", False))
            finally:
                if reopened is not None:
                    reopened.destroy()
                window.close_window()
                window.destroy()

    def test_independent_server_save_handles_main_window_changes(self):
        import threading
        from unittest.mock import Mock, patch
        from app import Window, Gtk

        for close_owner in (False, True):
            with self.subTest(close_owner=close_owner), tempfile.TemporaryDirectory() as directory:
                store = AliasStore(Path(directory) / "servers.json")
                store.save(["legacy-server"])
                window = Window(store=store, enable_quota=False)
                dialog = window.add_dialog()
                dialog.host.set_text("192.0.2.10")
                dialog.username.set_text("test-user")
                dialog.password.set_text("test-only-password")
                started, proceed = threading.Event(), threading.Event()
                application = Mock()

                def save_password(*_):
                    started.set()
                    if not proceed.wait(5):
                        raise RuntimeError("Test did not release the keyring worker")

                try:
                    with patch("app.credentials.save_password", side_effect=save_password) as save, \
                            patch("app.credentials.delete_password") as delete, \
                            patch.object(window, "get_application", return_value=application):
                        dialog.response(Gtk.ResponseType.OK)
                        pump_until(started.is_set)
                        if close_owner:
                            window.destroy()
                            self.assertTrue(dialog.closed)
                        else:
                            window.current = "legacy-server"
                            window.remove_alias()
                            self.assertEqual(store.load(), [])
                        proceed.set()
                        pump_until(lambda: not dialog.busy)
                        application.hold.assert_called_once_with()
                        application.release.assert_called_once_with()
                        if close_owner:
                            self.assertEqual(store.load(), ["legacy-server"])
                            delete.assert_called_once_with(save.call_args.args[0])
                        else:
                            self.assertEqual(store.load(), [save.call_args.args[0]])
                            self.assertEqual(window.aliases, store.load())
                            delete.assert_not_called()
                finally:
                    proceed.set()
                    window.destroy()

    def test_add_server_window_is_independent_and_reused(self):
        from app import Window, Gdk, Gtk

        with tempfile.TemporaryDirectory() as directory:
            window = Window(store=AliasStore(Path(directory) / "servers.json"), enable_quota=False,
                            command_factory=lambda _: ["/bin/cat"])
            window.confirm = lambda *_: True
            try:
                window.server_list.select_row(window.rows[""])
                session = window.current_session()
                pump_until(lambda: session.alive and window.get_allocated_width() > 100)
                main_size = window.get_size()
                pid = session.pid
                dialog = window.add_dialog()
                self.assertFalse(dialog.get_modal())
                self.assertIsNone(dialog.get_transient_for())
                self.assertEqual(dialog.get_type_hint(), Gdk.WindowTypeHint.NORMAL)
                self.assertIs(window.add_dialog(), dialog)
                dialog.host.set_text("192.0.2.10")
                self.assertIs(window.add_dialog(), dialog)
                self.assertEqual(dialog.host.get_text(), "192.0.2.10")
                dialog.move(80, 80)
                dialog.resize(480, 440)
                pump_until(lambda: dialog.get_size().width >= 480)
                self.assertEqual(window.get_size(), main_size)
                second = window.new_session()
                pump_until(lambda: second.alive)
                dialog.response(Gtk.ResponseType.CANCEL)
                self.assertTrue(dialog.closed)
                self.assertEqual(session.pid, pid)
                self.assertTrue(session.alive)
                replacement = window.add_dialog()
                self.assertIsNot(replacement, dialog)
                window.close_window()
                window.destroy()
                self.assertTrue(replacement.closed)
            finally:
                window.close_window()
                window.destroy()

    def test_tab_numbers_follow_positions_and_alt_shortcuts(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import Window, Gdk

        with tempfile.TemporaryDirectory() as directory:
            window = Window(store=AliasStore(Path(directory) / "servers.json"), enable_quota=False,
                            command_factory=lambda _: ["/bin/cat"])
            window.confirm = lambda *_: True
            try:
                window.server_list.select_row(window.rows[""])
                second = window.new_session()
                third = window.new_session()
                group = window.groups[""]
                first = group.get_nth_page(0)
                sessions = [first, second, third]
                pump_until(lambda: all(session.alive for session in sessions))
                pids = [session.pid for session in sessions]

                def assert_positions(expected):
                    self.assertEqual(group.get_children(), expected)
                    for position, session in enumerate(expected, 1):
                        self.assertEqual(session.tab_title, f"[{position}]")
                        self.assertTrue(session.tab_label.get_text().startswith(f"[{position}]"))
                        self.assertTrue(session.tab_label.get_tooltip_text().startswith(f"[{position}]"))
                        event = SimpleNamespace(state=Gdk.ModifierType.MOD1_MASK, keyval=ord(str(position)))
                        self.assertTrue(window.key_pressed(window, event))
                        self.assertIs(window.current_session(), session)

                # Exercise the same drop callback as a mouse drag, using stable session identity.
                data = SimpleNamespace(get_data=lambda: str(third.number).encode())
                with patch("app.Gtk.drag_finish"):
                    group.tab_dropped(group.tabs[first][1], object(), 0, 0, data, 0, 0, first)
                assert_positions([third, first, second])
                self.assertEqual([session.pid for session in sessions], pids)
                self.assertTrue(all(session.alive for session in sessions))

                # A second drag must still identify the original session after renumbering.
                data = SimpleNamespace(get_data=lambda: str(first.number).encode())
                with patch("app.Gtk.drag_finish"):
                    group.tab_dropped(group.tabs[second][1], object(), 0, 0, data, 0, 0, second)
                assert_positions([third, second, first])
                window.close_session(second)
                assert_positions([third, first])
                fourth = window.new_session()
                pump_until(lambda: fourth.alive)
                assert_positions([third, first, fourth])
                fourth.terminal.feed_child(b"\x04")
                pump_until(lambda: not fourth.active)
                group.reorder_child(fourth, 0)
                assert_positions([fourth, third, first])
                self.assertEqual(fourth.tab_label.get_text(), "[1] · 已结束")
                window.command_factory = lambda _: ["/zterm-test-nonexistent-executable"]
                failed = window.new_session()
                pump_until(lambda: not failed.active)
                group.reorder_child(failed, 1)
                assert_positions([fourth, failed, third, first])
                self.assertEqual(failed.tab_label.get_text(), "[2] · 失败")
            finally:
                window.close_window()
                window.destroy()

    def test_add_server_dialog_persists_display_and_connection_fields(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["legacy-server"])
            window = Window(store=store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
            window.confirm = lambda *_: True
            try:
                with patch("app.bash_ssh_aliases", return_value={"lab": {"user": "zhouyawei"}}):
                    dialog = window.add_dialog()
                self.assertEqual(dialog.port.get_value_as_int(), 22)
                self.assertEqual(dialog.username.get_text(), "zhouyawei")
                self.assertFalse(dialog.password.get_visibility())
                dialog.host.set_text("not-an-ip")
                dialog.response(Gtk.ResponseType.OK)
                self.assertIn("IP", dialog.error_label.get_text())
                self.assertEqual(window.aliases, ["legacy-server"])
                dialog.host.set_text("192.0.2.10")
                dialog.name.set_text("开发服务器")
                dialog.port.set_value(2222)
                with patch("app.credentials.save_password") as save:
                    dialog.response(Gtk.ResponseType.OK)
                    pump_until(lambda: dialog.closed)
                    save.assert_not_called()
                server_id = window.aliases[-1]
                profile = window.profiles[server_id]
                self.assertEqual(profile, {"name": "开发服务器", "host": "192.0.2.10",
                                          "username": "zhouyawei", "port": 2222, "password_saved": False})
                self.assertEqual(window.rows[server_id].get_child().get_text(), "开发服务器")
                self.assertIn("zhouyawei@192.0.2.10", window.rows[server_id].get_tooltip_text())
                self.assertEqual(window.default_command(server_id), server_ssh_command(profile))
                window.server_list.select_row(window.rows[server_id])
                pump_until(lambda: window.current_session().alive)
                self.assertEqual(window.header.get_title(), "开发服务器")
                self.assertEqual(window.connection_label.get_text(), "开发服务器 · 在线")
                window.search.set_text("192.0.2.10")
                window.server_list.invalidate_filter()
                self.assertTrue(window.rows[server_id].get_child_visible())
                self.assertFalse(window.rows["legacy-server"].get_child_visible())
                restored = AliasStore(store.path)
                self.assertEqual(restored.load(), window.aliases)
                self.assertEqual(restored.profiles, window.profiles)
                window.remove_alias()
                self.assertEqual(store.load(), ["legacy-server"])
                self.assertEqual(store.profiles, {})
            finally:
                window.close_window()
                window.destroy()

    def test_server_password_storage_failure_rollback_and_removal(self):
        from unittest.mock import patch
        from app import Window, Gtk

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save([])
            window = Window(store=store, enable_quota=False)
            dialog = window.add_dialog()
            dialog.host.set_text("192.0.2.10")
            dialog.username.set_text("zhouyawei")
            dialog.password.set_text("test-only-password")
            try:
                with patch("app.credentials.save_password", side_effect=RuntimeError("unavailable")):
                    dialog.response(Gtk.ResponseType.OK)
                    pump_until(lambda: not dialog.busy)
                self.assertFalse(dialog.closed)
                self.assertEqual(store.load(), [])
                with patch("app.credentials.save_password") as save, \
                        patch("app.credentials.delete_password") as delete, \
                        patch.object(store, "save", side_effect=OSError("read only")):
                    dialog.response(Gtk.ResponseType.OK)
                    pump_until(lambda: not dialog.busy)
                    delete.assert_called_once_with(save.call_args.args[0])
                self.assertEqual(store.load(), [])
                with patch("app.credentials.save_password") as save:
                    dialog.response(Gtk.ResponseType.OK)
                    pump_until(lambda: dialog.closed)
                    server_id = window.aliases[0]
                    save.assert_called_once_with(server_id, "192.0.2.10", "test-only-password")
                self.assertNotIn("test-only-password", store.path.read_text())
                self.assertTrue(store.profiles[server_id]["password_saved"])
                self.assertEqual(store.profiles[server_id]["name"], "192.0.2.10")
                duplicate = window.add_dialog()
                duplicate.host.set_text("192.0.2.10")
                duplicate.username.set_text("another-user")
                duplicate.response(Gtk.ResponseType.OK)
                self.assertIn("已存在", duplicate.error_label.get_text())
                duplicate.destroy()
                window.current = server_id
                with patch("app.credentials.delete_password") as delete:
                    window.remove_alias()
                    pump_until(lambda: delete.called)
                    delete.assert_called_once_with(server_id)
                self.assertEqual(store.load(), [])
            finally:
                dialog.destroy()
                window.destroy()

    def test_interface_theme_preview_is_scoped_and_persists(self):
        from app import AppearanceDialog, Window, rgba
        from gi.repository import GLib, Gtk

        context = GLib.MainContext.default()

        def pump_until(predicate):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("Interface colors did not settle")

        def background(widget):
            return widget.get_style_context().get_background_color(Gtk.StateFlags.NORMAL).to_string()

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "one/servers.json")
            other_store = AliasStore(Path(directory) / "two/servers.json")
            # A pre-existing configuration without the new field remains readable.
            legacy = default_appearance()
            legacy.pop("interface_theme")
            appearance = AppearanceStore(store.path.with_name("appearance.json"))
            appearance.path.parent.mkdir(parents=True)
            appearance.path.write_text(json.dumps(legacy))
            before = appearance.path.read_bytes()
            window = Window(enable_quota=False, store=store,
                            command_factory=lambda _: ["/bin/sh", "-c", "read line"])
            other = Window(enable_quota=False, store=other_store)
            dialog = None
            window.confirm = lambda *_: True
            try:
                original = window.appearance.copy()
                self.assertEqual(original["interface_theme"], "follow-terminal")
                window.server_list.select_row(window.rows[""])
                session = window.current_session()
                pump_until(lambda: session.alive)
                pid = session.pid
                original_background = session.terminal.get_color_background_for_draw().to_string()
                pump_until(lambda: background(other.header) == rgba(interface_colors(original)["surface"]).to_string())
                other_background = background(other.header)
                dialog = AppearanceDialog(window)
                for name in THEMES:
                    with self.subTest(interface_theme=name):
                        dialog.interface_theme.set_active_id(name)
                        colors = interface_colors(dialog.values())
                        expected = rgba(colors["surface"]).to_string()
                        pump_until(lambda: background(window.header) == expected)
                        for widget in (window.sidebar, window.status_bar, window.settings_menu,
                                       window.groups[""].tab_strip):
                            self.assertEqual(background(widget), expected)
                        self.assertEqual(background(other.header), other_background)
                        self.assertEqual(session.terminal.get_color_background_for_draw().to_string(),
                                         original_background)
                        self.assertEqual(window.appearance, original)
                        self.assertEqual(session.pid, pid)
                dialog.interface_theme.set_active_id("system")
                self.assertEqual(window.interface_style.provider.to_string(), "")
                dialog.interface_theme.set_active_id("solarized-dark")
                dialog.response(Gtk.ResponseType.CANCEL)
                dialog = None
                pump_until(lambda: background(window.header) == other_background)
                self.assertEqual(appearance.path.read_bytes(), before)

                dialog = AppearanceDialog(window)
                dialog.interface_theme.set_active_id("gruvbox-light")
                expected = dialog.values()
                dialog.response(Gtk.ResponseType.OK)
                dialog = None
                self.assertEqual(appearance.load(), expected)
                self.assertEqual(session.pid, pid)
                window.close_window()
                window.destroy()
                window = Window(enable_quota=False, store=store)
                self.assertEqual(window.appearance, expected)
                pump_until(lambda: background(window.header) == rgba(interface_colors(expected)["surface"]).to_string())
                dialog = AppearanceDialog(window)
                dialog.interface_theme.set_active_id("follow-terminal")
                dialog.theme.set_active_id("solarized-dark")
                pump_until(lambda: background(window.header) == rgba("#08313c").to_string())
                # Closing the dialog through the window manager also cancels preview.
                dialog.destroy()
                dialog = None
                pump_until(lambda: background(window.header) == rgba(interface_colors(expected)["surface"]).to_string())
            finally:
                if dialog is not None:
                    dialog.destroy()
                window.confirm = lambda *_: True
                window.close_window()
                window.destroy()
                other.destroy()
                while context.pending():
                    context.iteration(False)

    def test_inline_add_tracks_tabs_reordering_overflow_and_empty_group(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app import SessionNotebook
        from gi.repository import GLib, Gtk

        context = GLib.MainContext.default()

        def pump_until(predicate):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("Tab strip did not reach the expected layout")

        counter = 0

        def add_page():
            nonlocal counter
            counter += 1
            page = Gtk.Box()
            page.number = counter
            page.tab_label = Gtk.Label(label=f"[{counter}]")
            page.set_tab_position = lambda position: page.tab_label.set_text(f"[{position}]")
            page.terminal = Gtk.Entry()
            page.add(page.terminal)
            page.show_all()
            index = group.append_page(page, None)
            group.add_tab(page, lambda session: session.destroy())
            group.set_current_page(index)

        group = SessionNotebook(add_page)
        window = Gtk.Window(default_width=480, default_height=200)
        window.add(group.panel)
        window.show_all()
        try:
            group.add_button.clicked()
            group.add_button.clicked()
            first, second = group.get_children()

            def inline_gap():
                last = group.get_nth_page(group.get_n_pages() - 1)
                row = group.tabs[last][0]
                right = row.translate_coordinates(group.tab_strip, row.get_allocated_width(), 0)[0]
                return group.add_button.translate_coordinates(group.tab_strip, 0, 0)[0] - right

            # Before GTK allocates the rows, their initial 1px sizes can also
            # satisfy the gap check. Wait for actual layout before measuring.
            pump_until(lambda: all(group.tabs[page][0].get_allocated_width() >= 160
                                   for page in (first, second)) and 0 <= inline_gap() <= 6)
            self.assertLess(group.add_button.translate_coordinates(window, 0, 0)[0], 400)
            for page in (first, second):
                row, select = group.tabs[page]
                close = row.get_children()[1]
                self.assertGreaterEqual(row.get_allocated_width(), 160)
                self.assertGreater(select.get_allocated_width(), close.get_allocated_width() * 3)
                close_right = close.translate_coordinates(row, close.get_allocated_width(), 0)[0]
                self.assertGreaterEqual(row.get_allocated_width() - close_right, 0)
                self.assertLessEqual(row.get_allocated_width() - close_right, 8)
            group.tabs[first][1].clicked()
            self.assertIs(group.get_nth_page(group.get_current_page()), first)
            self.assertEqual(group.get_n_pages(), 2, "Selecting a tab must not close it")
            self.assertTrue(group.tabs[first][0].get_style_context().has_class("active"))
            self.assertFalse(group.tabs[second][0].get_style_context().has_class("active"))
            group.tabs[first][1].clicked()
            self.assertTrue(group.tabs[first][1].get_active(), "Clicking the active tab must keep it selected")
            data = SimpleNamespace(get_data=lambda: str(second.number).encode())
            with patch("app.Gtk.drag_finish") as finish:
                group.tab_dropped(group.tabs[first][1], object(), 0, 0, data, 0, 0, first)
                self.assertTrue(finish.call_args.args[1])
            self.assertIs(group.get_nth_page(0), second)
            self.assertEqual([page.tab_label.get_text() for page in group.get_children()], ["[1]", "[2]"])
            self.assertEqual(group.tab_strip.get_children(),
                             [group.tabs[second][0], group.tabs[first][0], group.add_button])
            pump_until(lambda: 0 <= inline_gap() <= 6)

            for _ in range(15):
                group.add_button.clicked()
            adjustment = group.tab_scroll.get_hadjustment()
            pump_until(lambda: adjustment.get_upper() > adjustment.get_page_size()
                       and group.add_button.translate_coordinates(group.tab_scroll, 0, 0)[0]
                       + group.add_button.get_allocated_width() <= group.tab_scroll.get_allocated_width())
            self.assertLessEqual(window.get_allocated_width(), 500, "Tabs must scroll instead of widening the window")
            group.set_current_page(0)
            pump_until(lambda: adjustment.get_value() == 0)
            last = group.get_nth_page(group.get_n_pages() - 1)
            group.tabs[last][0].get_children()[1].clicked()
            pump_until(lambda: 0 <= inline_gap() <= 6)
            for page in group.get_children():
                page.destroy()
            pump_until(lambda: group.get_n_pages() == 0 and group.add_button.get_mapped())
            self.assertEqual(group.tab_strip.get_children(), [group.add_button])
            group.add_button.clicked()
            self.assertEqual(group.get_n_pages(), 1)
            self.assertEqual(group.get_nth_page(0).tab_label.get_text(), "[1]")
        finally:
            window.destroy()
            while context.pending():
                context.iteration(False)

    def test_named_themes_render_ansi_colors_and_persist(self):
        from app import AppearanceDialog, Window
        from gi.repository import Gdk, GLib, Gtk

        context = GLib.MainContext.default()
        names = list(THEMES)
        with tempfile.TemporaryDirectory() as directory:
            window = Window(enable_quota=False, store=AliasStore(Path(directory) / "servers.json"))
            dialog = None
            try:
                for name in names:
                    with self.subTest(theme=name):
                        dialog = AppearanceDialog(window)
                        dialog.theme.set_active_id(name)
                        self.assertEqual(dialog.theme.get_active_text(), THEMES[name]["name"])
                        self.assertEqual(dialog.values()["background"], THEMES[name]["background"])
                        terminal = dialog.preview
                        terminal.reset(True, True)
                        # Clear after any sample text still queued by VTE.feed().
                        swatches = "\x1b[2J\x1b[H\x1b[?25l" + "".join(f"\x1b[{40 + i}m  " for i in range(8))
                        swatches += "\x1b[0m\r\n" + "".join(f"\x1b[{100 + i}m  " for i in range(8))
                        terminal.feed((swatches + "\x1b[0m\r\n  ██").encode())
                        expected = [tuple(bytes.fromhex(color[1:])) for color in THEMES[name]["palette"]]
                        expected += [tuple(bytes.fromhex(THEMES[name][key][1:]))
                                     for key in ("background", "foreground")]
                        deadline = time.monotonic() + 3
                        actual = []
                        while time.monotonic() < deadline:
                            while context.pending():
                                context.iteration(False)
                            if terminal.get_realized():
                                x_origin, y_origin = terminal.translate_coordinates(dialog, 0, 0)
                                pixbuf = Gdk.pixbuf_get_from_window(dialog.get_window(), x_origin, y_origin,
                                                                  terminal.get_allocated_width(),
                                                                  terminal.get_allocated_height())
                                pixels = pixbuf.get_pixels()
                                actual = []
                                for i in range(18):
                                    x = (2 * (i % 8) + 1) * terminal.get_char_width()
                                    y = (i // 8) * terminal.get_char_height() + terminal.get_char_height() // 2
                                    offset = y * pixbuf.get_rowstride() + x * pixbuf.get_n_channels()
                                    actual.append(tuple(pixels[offset:offset + 3]))
                                if actual == expected:
                                    break
                            time.sleep(0.02)
                        self.assertEqual(actual, expected, "Rendered ANSI and default colors must match the theme")
                        dialog.response(Gtk.ResponseType.OK)
                        saved = AppearanceStore(window.appearance_store.path).load()
                        self.assertEqual(saved["theme"], name)
                        self.assertEqual(saved, window.appearance)
                        dialog = None
                self.assertEqual(window.groups, {}, "Theme previews must not launch terminal processes")
            finally:
                if dialog is not None:
                    dialog.destroy()
                window.close_window()
                window.destroy()
                while context.pending():
                    context.iteration(False)

    def test_appearance_preview_save_cancel_and_restart(self):
        from unittest.mock import patch
        from app import AppearanceDialog, Window, rgba
        from gi.repository import Gdk, GLib, Gtk, Pango

        context = GLib.MainContext.default()

        def pump_until(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("Timed out waiting for appearance test sessions")

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["dev-server"])
            window = Window(enable_quota=False, store=store, command_factory=lambda alias: ["/bin/sh", "-c", "read line"])
            window.confirm = lambda *args: True
            dialogs = []
            try:
                window.server_list.select_row(window.rows[""])
                local = window.groups[""].get_nth_page(0)
                window.server_list.select_row(window.rows["dev-server"])
                remote = window.groups["dev-server"].get_nth_page(0)
                pump_until(lambda: local.alive and remote.alive)
                pids = [local.pid, remote.pid]
                original = window.appearance.copy()
                dialog = AppearanceDialog(window)
                dialogs.append(dialog)
                dialog.theme.set_active_id("gnome-black-on-white")
                dialog.font.set_font("Monospace Bold 16")
                dialog.font.emit("font-set")
                self.assertEqual(dialog.preview.get_font().get_size(), 16 * Pango.SCALE)
                self.assertEqual(window.appearance, original)
                dialog.response(Gtk.ResponseType.CANCEL)
                self.assertFalse(window.appearance_store.path.exists())

                dialog = AppearanceDialog(window)
                dialogs.append(dialog)
                dialog.theme.set_active_id("gnome-black-on-white")
                dialog.font.set_font("Monospace Bold 16")
                dialog.colors["background"].set_rgba(rgba("#e0e8ef"))
                dialog.colors["background"].emit("color-set")
                expected = dialog.values()
                with patch.object(window.appearance_store, "save", side_effect=OSError("disk full")):
                    dialog.response(Gtk.ResponseType.OK)
                self.assertIn("disk full", dialog.error.get_text())
                self.assertEqual(window.appearance, original)
                self.assertTrue(dialog.get_visible())
                dialog.response(Gtk.ResponseType.OK)
                self.assertEqual(window.appearance_store.load(), expected)
                self.assertEqual(window.appearance, expected)
                self.assertEqual([local.pid, remote.pid], pids, "Appearance must not reconnect sessions")
                added = window.new_session()
                pump_until(lambda: added.alive)
                for session in (local, remote, added):
                    self.assertEqual(session.terminal.get_font().get_size(), 16 * Pango.SCALE)
                    self.assertEqual(session.terminal.get_font().get_weight(), Pango.Weight.BOLD)
                    self.assertEqual(session.terminal.get_color_background_for_draw().to_string(),
                                     rgba("#e0e8ef").to_string())

                dialog = AppearanceDialog(window)
                dialogs.append(dialog)
                self.assertEqual(dialog.values(), expected)
                dialog.response(Gtk.ResponseType.REJECT)
                self.assertEqual(dialog.values(), default_appearance())
                self.assertEqual(window.appearance, expected)
                dialog.set_values(expected)
                pump_until(lambda: dialog.get_window() is not None)
                # This preview uses a separate VTE with sample text and no process.
                time.sleep(0.1)
                while context.pending():
                    context.iteration(False)
                pixbuf = Gdk.pixbuf_get_from_window(dialog.get_window(), 0, 0,
                                                   dialog.get_allocated_width(), dialog.get_allocated_height())
                pixbuf.savev(str(Path(__file__).parent / "appearance-preview.png"), "png", [], [])
                dialog.response(Gtk.ResponseType.CANCEL)
                pids.append(added.pid)
                window.close_window()
                window.destroy()
                pump_until(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))
                window = Window(enable_quota=False, store=store, command_factory=lambda alias: ["/bin/sh", "-c", "read line"])
                window.confirm = lambda *args: True
                self.assertEqual(window.appearance, expected)
                self.assertEqual(window.aliases, ["dev-server"])
                self.assertEqual(window.groups, {})
            finally:
                for dialog in dialogs:
                    dialog.destroy()
                pids = [page.pid for group in window.groups.values() for page in group.get_children()
                        if page.pid]
                window.close_window()
                window.destroy()
                pump_until(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))

    def test_startup_failure_and_close_pending(self):
        from unittest.mock import patch
        from app import Session, Window
        from gi.repository import GLib

        context = GLib.MainContext.default()
        callbacks = []
        original = Session.spawned

        def spawned(session, terminal, pid, error, *args):
            original(session, terminal, pid, error, *args)
            callbacks.append((session, pid))

        def pump_until(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("Timed out waiting for spawn callback or child cleanup")

        with tempfile.TemporaryDirectory() as directory, patch.object(Session, "spawned", spawned):
            window = Window(enable_quota=False, store=AliasStore(Path(directory) / "servers.json"),
                            command_factory=lambda alias: [str(Path(directory) / "missing")])
            window.confirm = lambda *args: True
            try:
                window.server_list.select_row(window.rows[""])
                failed = window.groups[""].get_nth_page(0)
                pump_until(lambda: any(session is failed for session, _ in callbacks))
                self.assertFalse(failed.active)
                self.assertIn("启动失败", window.connection_label.get_tooltip_text())
                self.assertTrue(window.reconnect.get_visible())
                self.assertEqual(window.reconnect.get_label(), "重新打开")
                window.close_session(failed)
                self.assertEqual(window.groups[""].get_n_pages(), 0)
                self.assertEqual(window.connection_label.get_text(), "本地终端 · 无会话")
                self.assertFalse(window.reconnect.get_visible())

                window.command_factory = lambda alias: ["/bin/sh", "-c", "read line"]
                pending = window.new_session()
                self.assertTrue(pending.pending)
                self.assertIn("正在连接", window.connection_label.get_text())
                self.assertFalse(window.reconnect.get_visible())
                window.close_session(pending)
                self.assertTrue(pending.closed)
                pump_until(lambda: any(session is pending for session, _ in callbacks))
                self.assertFalse(pending.active)
                self.assertEqual(window.groups[""].get_n_pages(), 0)
                for session, pid in callbacks:
                    if session is pending and pid > 0:
                        pump_until(lambda: not Path(f"/proc/{pid}").exists())
            finally:
                window.close_window()
                window.destroy()
                while context.pending():
                    context.iteration(False)

    def test_real_pty_tabs_reconnect_and_cleanup(self):
        from unittest.mock import patch
        from app import Window
        from gi.repository import Gdk, GLib, Gtk, Vte

        context = GLib.MainContext.default()

        def pump_until(predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if predicate():
                    return
                time.sleep(0.01)
            self.fail("Timed out waiting for VTE state")

        def output(session):
            return session.terminal.get_text_format(Vte.Format.TEXT)

        with tempfile.TemporaryDirectory() as directory:
            store = AliasStore(Path(directory) / "servers.json")
            store.save(["dev-server", "build-server"])
            command = ["/bin/sh", "-c",
                       "printf 'READY\\n'; while IFS= read -r line; do "
                       "[ \"$line\" = exit ] && exit 7; printf 'REPLY=%s\\n' \"$line\"; done"]
            window = Window(enable_quota=False, store=store, command_factory=lambda alias: command)
            window.confirm = lambda *args: True
            try:
                self.assertEqual(window.groups, {}, "Startup must not connect automatically")
                menu_items = window.settings_menu.get_children()
                self.assertEqual([item.get_label() for item in menu_items],
                                 ["快捷键", "外观设置", "订阅额度"])
                for item, method in zip(menu_items[:1], ("show_shortcuts",)):
                    with patch.object(window, method) as action:
                        item.activate()
                        action.assert_called_once_with()
                with patch("app.AppearanceDialog") as appearance:
                    menu_items[1].activate()
                    appearance.assert_called_once_with(window)
                with patch.object(window, "add_dialog") as action:
                    window.add_server_button.clicked()
                    action.assert_called_once_with()
                window.server_list.select_row(window.rows["dev-server"])
                with patch.object(window, "edit_dialog") as edit:
                    window.edit_button.clicked()
                    edit.assert_called_once_with("dev-server")
                first = window.groups["dev-server"].get_nth_page(0)
                pump_until(lambda: first.alive and "READY" in output(first))
                self.assertEqual(first.tab_label.get_text(), "[1]")
                self.assertEqual(window.header.get_title(), "dev-server")
                self.assertEqual(window.get_title(), "dev-server")
                self.assertEqual(window.connection_label.get_text(), "dev-server · 在线")
                self.assertFalse(window.reconnect.get_visible())
                self.assertEqual(len(first.get_children()), 1, "Sessions must not have a second footer")
                first.terminal.feed_child(b"hello-terminal\n")
                pump_until(lambda: "REPLY=hello-terminal" in output(first))
                group = window.groups["dev-server"]
                group.add_button.clicked()
                second = group.get_nth_page(1)
                pump_until(lambda: second.alive)
                self.assertEqual(second.tab_label.get_text(), "[2]")
                pids_before_toggle = (first.pid, second.pid)
                original_width = first.terminal.get_allocated_width()
                window.sidebar_toggle.set_active(False)
                pump_until(lambda: not window.sidebar.get_visible()
                           and first.terminal.get_allocated_width() > original_width)
                window.sidebar_toggle.set_active(True)
                pump_until(lambda: window.sidebar.get_visible()
                           and first.terminal.get_allocated_width() == original_width)
                self.assertEqual((first.pid, second.pid), pids_before_toggle)
                window.server_list.select_row(window.rows["build-server"])
                self.assertEqual(window.header.get_title(), "build-server")
                remote = window.groups["build-server"].get_nth_page(0)
                pump_until(lambda: remote.alive)
                self.assertEqual(window.connection_label.get_text(), "build-server · 在线")
                window.server_list.select_row(window.rows["dev-server"])
                self.assertIs(window.stack.get_visible_child(), window.groups["dev-server"].panel)
                # A late response from another server must never replace the visible account.
                window.quota_received("dev-server", {
                    "codex": {"windows": [{"label": "5h", "used": 24, "reset": time.time() + 3600}]}})
                window.quota_received("build-server", {
                    "codex": {"message": "OTHER-SERVER"}})
                self.assertIn("24%", window.quota_label.get_text())
                self.assertNotIn("OTHER-SERVER", window.quota_label.get_text())
                self.assertTrue(first.alive and second.alive and remote.alive)
                self.assertEqual(window.groups["dev-server"].get_n_pages(), 2)
                first.terminal.feed_child(b"exit\n")
                pump_until(lambda: not first.active)
                self.assertIn("退出码 7", first.status_detail)
                self.assertEqual(window.connection_label.get_text(), "dev-server · 在线",
                                 "A background tab exiting must not change the active tab status")
                self.assertFalse(window.reconnect.get_visible())
                group.set_current_page(0)
                self.assertTrue(window.reconnect.get_visible())
                self.assertEqual(window.reconnect.get_label(), "重新连接")
                self.assertEqual(window.connection_label.get_text(), "dev-server · ")
                self.assertIn("退出码 7", window.connection_label.get_tooltip_text())
                self.assertEqual(first.tab_label.get_text(), "[1] · 已结束")
                self.assertIn("REPLY=hello-terminal", output(first))
                window.reconnect.clicked()
                self.assertFalse(window.reconnect.get_visible())
                pump_until(lambda: first.alive)
                self.assertEqual(window.connection_label.get_text(), "dev-server · 在线")
                self.assertEqual(first.tab_label.get_text(), "[1]")
                self.assertEqual(window.header.get_title(), "dev-server")
                self.assertEqual(window.get_title(), "dev-server")
                window.confirm = lambda *args: False
                window.close_session(second)
                self.assertEqual(window.groups["dev-server"].get_n_pages(), 2)
                window.confirm = lambda *args: True
                second_pid = second.pid
                window.close_session(second)
                pump_until(lambda: not Path(f"/proc/{second_pid}").exists())
                self.assertEqual(window.groups["dev-server"].get_n_pages(), 1)
                for _ in range(3):
                    group.add_button.clicked()
                pump_until(lambda: all(page.alive for page in window.groups["dev-server"].get_children()))
                window.groups["dev-server"].set_current_page(0)
                # Screenshot contains simulated sessions only, never user data.
                while context.pending():
                    context.iteration(False)
                time.sleep(0.15)
                while context.pending():
                    context.iteration(False)
                pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0,
                                                   window.get_allocated_width(), window.get_allocated_height())
                self.assertFalse(first.tab_label.get_layout().is_ellipsized(),
                                 "A normal server name and session number must be visible")
                self.assertIs(window.quota_label.get_parent(), window.status_bar)
                self.assertIs(window.connection_label.get_parent().get_parent(), window.status_bar)
                quota_x = window.quota_label.translate_coordinates(window.status_bar, 0, 0)[0]
                self.assertAlmostEqual(quota_x + window.quota_label.get_allocated_width() / 2,
                                       window.status_bar.get_allocated_width() / 2, delta=2)
                pixbuf.savev(str(Path(__file__).parent / "preview.png"), "png", [], [])
                remote_pid = remote.pid
                window.server_list.select_row(window.rows["build-server"])
                window.remove_alias()
                pump_until(lambda: not Path(f"/proc/{remote_pid}").exists())
                self.assertEqual(store.load(), ["dev-server"])
                self.assertNotIn("build-server", window.groups)
                self.assertEqual(window.header.get_title(), "zTerm")
                self.assertIn("选择服务器", window.quota_label.get_text())
                self.assertEqual(window.connection_label.get_text(), "未选择服务器")
                self.assertFalse(window.reconnect.get_visible())
                window.server_list.select_row(window.rows[""])
                self.assertEqual(window.header.get_title(), "本地终端")
                local = window.groups[""].get_nth_page(0)
                pump_until(lambda: local.alive)
                self.assertTrue(first.alive)
                pids = [page.pid for group in window.groups.values() for page in group.get_children()]
                self.assertFalse(window.close_window())
                pump_until(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))
            finally:
                window.confirm = lambda *args: True
                window.close_window()
                window.destroy()
                while context.pending():
                    context.iteration(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
