"""Model tests and real VTE lifecycle checks, run under xvfb-run."""

import os
import json
from pathlib import Path
import tempfile
import time
import unittest

from model import (AliasStore, AppearanceStore, THEMES, default_appearance, interface_colors,
                   discover_aliases, ssh_command, valid_alias)


class ModelTests(unittest.TestCase):
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

    def test_includes_cycles_comments_and_wildcards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").write_text('Host dev prod # names\nInclude hosts/*\nHost *.wild !excluded\nHost=third\n')
            (root / "hosts").mkdir()
            (root / "hosts/one").write_text('Host "included" dev\nInclude config\n')
            self.assertEqual(discover_aliases(root / "config"), ["dev", "prod", "included", "third"])

    def test_arguments_cannot_be_ssh_options_or_shell_commands(self):
        for alias in ["-F", "host;touch /tmp/x", "a b", "$(id)", "", "host\nname"]:
            self.assertFalse(valid_alias(alias))
            with self.assertRaises(ValueError):
                ssh_command(alias)
        self.assertEqual(ssh_command("dev-one"), ["/usr/bin/ssh", "dev-one"])


class TerminalTests(unittest.TestCase):
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

            pump_until(lambda: 0 <= inline_gap() <= 6)
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
                                 ["添加服务器", "导入别名", "快捷键", "外观设置", "订阅额度"])
                for item, method in zip(menu_items[:3], ("add_dialog", "import_aliases", "show_shortcuts")):
                    with patch.object(window, method) as action:
                        item.activate()
                        action.assert_called_once_with()
                with patch("app.AppearanceDialog") as appearance:
                    menu_items[3].activate()
                    appearance.assert_called_once_with(window)
                window.server_list.select_row(window.rows["dev-server"])
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
