#!/usr/bin/python3
"""zTerm — a GTK/VTE terminal with per-server session tabs."""

import os
from pathlib import Path
import pwd
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, Vte

from model import (AliasStore, AppearanceStore, THEMES, INTERFACE_THEMES,
                   default_appearance, discover_aliases, interface_colors, ssh_command, valid_alias)
from quota import fetch as fetch_quota, display as display_quota


def button(label, callback, tooltip=None):
    widget = Gtk.Button(label=label)
    widget.connect("clicked", callback)
    if tooltip:
        widget.set_tooltip_text(tooltip)
    return widget


def rgba(value):
    color = Gdk.RGBA()
    color.parse(value)
    return color


def apply_appearance(terminal, settings):
    terminal.set_font(Pango.FontDescription(settings["font"]))
    palette = THEMES[settings["theme"]]["palette"]
    terminal.set_colors(rgba(settings["foreground"]), rgba(settings["background"]),
                        [rgba(value) for value in palette])
    terminal.set_color_cursor(rgba(settings["cursor"]))
    terminal.set_color_cursor_foreground(rgba(settings["background"]))


class InterfaceStyle:
    """Window-scoped GTK colors shared with that window's menus and dialogs."""

    def __init__(self, window):
        self.css_class = f"zterm-ui-{id(self)}"
        self.provider = Gtk.CssProvider()
        self.screen = window.get_screen()
        Gtk.StyleContext.add_provider_for_screen(
            self.screen, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.attach(window)
        window.connect("destroy", self.dispose)

    def attach(self, widget):
        widget.get_style_context().add_class(self.css_class)

    def dispose(self, *_):
        Gtk.StyleContext.remove_provider_for_screen(self.screen, self.provider)

    def apply(self, settings):
        colors = interface_colors(settings)
        if colors is None:
            self.provider.load_from_data(b"")
            return
        css = """
            @scope { background-color: @background; color: @foreground; }
            @scope headerbar, @scope .workspace-sidebar, @scope .workspace-status,
            @scope .workspace-tabs, @scope menu, menu@scope {
                background: @surface; color: @foreground; border-color: @border;
                box-shadow: none;
            }
            @scope label { color: inherit; text-shadow: none; }
            @scope list, @scope viewport, @scope notebook, @scope stack {
                background-color: @background; color: @foreground;
            }
            @scope button, @scope entry {
                background: @surface; color: @foreground; border-color: @border;
                box-shadow: none; text-shadow: none;
            }
            @scope button.flat { background: transparent; border-color: transparent; }
            @scope button:hover, @scope row:hover, @scope menuitem:hover {
                background: @hover; color: @foreground;
            }
            @scope button:checked, @scope button:active, @scope row:selected,
            @scope entry selection {
                background: @selection; color: @foreground;
            }
            @scope button:disabled { color: alpha(@foreground, 0.45); }
            @scope entry:focus { border-color: @accent; }
            @scope separator, @scope paned > separator { background: @border; }
            @scope scrollbar { background: @surface; }
            @scope scrollbar slider { background: @border; border-color: @border; }
            @scope scrollbar slider:hover { background: @accent; }
            @scope .session-tab { background: @surface; border-color: @border; }
            @scope .session-tab.active {
                background: @selection; border-bottom-color: @accent;
                box-shadow: inset 0 -2px @accent;
            }
            @scope .session-tab-select, @scope .session-tab-close {
                background: none; border: none; box-shadow: none;
            }
            @scope .session-tab-close:hover { background: @hover; }
        """
        for key, color in colors.items():
            css = css.replace("@" + key, color)
        self.provider.load_from_data(css.replace("@scope", "." + self.css_class).encode())


class AppearanceDialog(Gtk.Dialog):
    def __init__(self, window):
        super().__init__(title="外观设置", transient_for=window, modal=True)
        self.owner = window
        window.interface_style.attach(self)
        self.connect("destroy", lambda *_: window.interface_style.apply(window.appearance))
        self.loading = True
        self.set_default_size(600, 540)
        self.add_buttons("恢复默认", Gtk.ResponseType.REJECT,
                         "取消", Gtk.ResponseType.CANCEL, "保存并应用", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        content = self.get_content_area()
        content.set_border_width(16)
        content.set_spacing(12)
        grid = Gtk.Grid(column_spacing=16, row_spacing=12)
        self.font = Gtk.FontButton(title="选择终端字体和字号", show_size=True, show_style=True)
        self.font.set_hexpand(True)
        self.font.set_preview_text("终端字体预览  AaBb 0123456789  ~/workspace $")
        grid.attach(Gtk.Label(label="字体与字号", xalign=0), 0, 0, 1, 1)
        grid.attach(self.font, 1, 0, 1, 1)
        self.theme = Gtk.ComboBoxText()
        for key, theme in THEMES.items():
            self.theme.append(key, theme["name"])
        grid.attach(Gtk.Label(label="终端配色", xalign=0), 0, 1, 1, 1)
        grid.attach(self.theme, 1, 1, 1, 1)
        self.interface_theme = Gtk.ComboBoxText()
        for key, name in INTERFACE_THEMES.items():
            self.interface_theme.append(key, name)
        grid.attach(Gtk.Label(label="界面主题", xalign=0), 0, 2, 1, 1)
        grid.attach(self.interface_theme, 1, 2, 1, 1)
        self.colors = {}
        for row, (key, label) in enumerate((("foreground", "文字颜色"),
                                           ("background", "背景颜色"), ("cursor", "光标颜色")), 3):
            control = Gtk.ColorButton(title=label, use_alpha=False)
            self.colors[key] = control
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(control, 1, row, 1, 1)
            control.connect("color-set", self.update_preview)
        content.pack_start(grid, False, False, 0)
        content.pack_start(Gtk.Label(label="界面主题控制标题栏、侧栏、标签和底栏，可跟随终端或独立选择。\n"
                                          "切换时预览界面；取消恢复原样，保存后应用到所有会话。",
                                    xalign=0, wrap=True), False, False, 0)
        self.preview = Vte.Terminal()
        self.preview.set_size(52, 5)
        self.preview.set_size_request(-1, 160)
        self.preview.set_input_enabled(False)
        self.preview.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
        self.preview.feed("终端预览  AaBb 0123456789\r\n"
                          "\x1b[32muser@workbench\x1b[0m:~$ ls\r\n"
                          "\x1b[34mworkspace/\x1b[0m  \x1b[33mnotes.txt\x1b[0m  "
                          "\x1b[31merror\x1b[0m\r\n$ ".encode())
        content.pack_start(self.preview, True, True, 0)
        self.error = Gtk.Label(xalign=0, wrap=True)
        content.pack_start(self.error, False, False, 0)
        self.theme.connect("changed", self.theme_changed)
        self.interface_theme.connect("changed", self.update_preview)
        self.font.connect("font-set", self.update_preview)
        self.set_values(window.appearance)
        self.connect("response", self.respond)
        self.show_all()

    def set_values(self, settings):
        self.loading = True
        self.font.set_font(settings["font"])
        self.interface_theme.set_active_id(settings.get("interface_theme", "follow-terminal"))
        self.theme.set_active_id(settings["theme"])
        for key, control in self.colors.items():
            control.set_rgba(rgba(settings[key]))
        self.loading = False
        self.update_preview()

    def values(self):
        settings = {"font": self.font.get_font(), "theme": self.theme.get_active_id(),
                    "interface_theme": self.interface_theme.get_active_id()}
        for key, control in self.colors.items():
            color = control.get_rgba()
            settings[key] = "#{:02x}{:02x}{:02x}".format(
                *[round(channel * 255) for channel in (color.red, color.green, color.blue)])
        return settings

    def theme_changed(self, *_):
        theme = THEMES[self.theme.get_active_id()]
        for key, control in self.colors.items():
            control.set_rgba(rgba(theme[key]))
        self.update_preview()

    def update_preview(self, *_):
        if self.loading:
            return
        apply_appearance(self.preview, self.values())
        self.owner.interface_style.apply(self.values())

    def respond(self, _, response):
        if response == Gtk.ResponseType.REJECT:
            self.set_values(default_appearance())
        elif response == Gtk.ResponseType.OK:
            if self.owner.appearance_error:
                self.error.set_text("请先修复外观配置并重启，避免覆盖原有设置。\n" +
                                    self.owner.appearance_error)
                return
            settings = self.values()
            try:
                self.owner.appearance_store.save(settings)
            except (OSError, ValueError) as error:
                self.error.set_text("保存失败：" + str(error))
                return
            self.owner.set_appearance(settings)
            self.destroy()
        else:
            self.destroy()


class Session(Gtk.Box):
    def __init__(self, window, alias, number, argv):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window, self.alias, self.number, self.argv = window, alias, number, argv
        self.alive = False
        self.pending = False
        self.closed = False
        self.pid = None
        self.tab_title = f"[{number}]"
        self.tab_label = Gtk.Label(label=self.tab_title)
        self.tab_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.tab_label.set_width_chars(min(len(self.tab_title), 32))
        self.tab_label.set_max_width_chars(32)
        self.tab_label.set_tooltip_text(self.tab_title)
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(20000)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_mouse_autohide(True)
        apply_appearance(self.terminal, window.appearance)
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.pack_start(self.terminal, True, True, 0)
        body.pack_start(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                     adjustment=self.terminal.get_vadjustment()), False, False, 0)
        self.pack_start(body, True, True, 0)
        self.status_detail = "正在启动…"
        self.terminal.connect("child-exited", self.exited)
        self.terminal.connect("window-title-changed", self.title_changed)
        self.terminal.connect("button-press-event", self.context_menu)
        self.show_all()
        self.start()

    @property
    def active(self):
        return self.alive or self.pending

    def start(self):
        if self.closed or self.active:
            return
        self.pending = True
        self.pid = None
        self.cancel = Gio.Cancellable()
        self.status_detail = "正在启动 " + (self.alias or "本地终端") + "…"
        self.tab_label.set_text(self.tab_title)
        self.window.render_status()
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, str(Path.home()), self.argv,
                [f"{key}={value}" for key, value in env.items()],
                GLib.SpawnFlags.DEFAULT, None, None, -1, self.cancel, self.spawned, None)
        except GLib.Error as error:
            self.spawned(self.terminal, -1, error, None)

    def spawned(self, terminal, pid, error, *_):
        self.pending = False
        if self.closed:
            return
        if error:
            self.status_detail = "启动失败：" + error.message
            self.tab_label.set_text(self.tab_title + " · 失败")
        else:
            self.pid = pid
            self.alive = True
            self.status_detail = (self.alias or "本地终端") + " · 会话运行中"
            terminal.grab_focus()
        self.window.render_status()

    def exited(self, terminal, status):
        self.alive = self.pending = False
        self.pid = None
        if self.closed:
            return
        code = os.waitstatus_to_exitcode(status)
        self.status_detail = f"会话已结束 · 退出码 {code} · 输出已保留"
        self.tab_label.set_text(self.tab_title + " · 已结束")
        self.window.render_status()

    def title_changed(self, terminal):
        title = terminal.get_window_title()
        self.tab_label.set_tooltip_text(self.tab_title + ("\n" + title if title else ""))

    def context_menu(self, terminal, event):
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        self.window.interface_style.attach(menu)
        for name, action in [("复制", lambda _: terminal.copy_clipboard_format(Vte.Format.TEXT)),
                             ("粘贴", lambda _: terminal.paste_clipboard()),
                             ("全选", lambda _: terminal.select_all())]:
            item = Gtk.MenuItem(label=name)
            item.connect("activate", action)
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def dispose(self):
        self.closed = True
        self.cancel.cancel()
        # Destroying the terminal releases its PTY and hangs up the child.
        self.destroy()


class SessionNotebook(Gtk.Notebook):
    """Native session pages with a scrollable, sequential tab-and-add strip."""

    def __init__(self, new_session):
        super().__init__(show_tabs=False, show_border=False)
        self.tabs = {}
        self.tab_css = Gtk.CssProvider()
        self.tab_css.load_from_data(b"""
            .session-tab {
                border: 1px solid alpha(@theme_fg_color, 0.18);
                border-radius: 5px 5px 0 0;
                background-color: alpha(@theme_fg_color, 0.04);
            }
            .session-tab.active {
                background-color: @theme_base_color;
                border-bottom-color: @theme_selected_bg_color;
                box-shadow: inset 0 -2px @theme_selected_bg_color;
            }
            .session-tab-select, .session-tab-close {
                background: none;
                border: none;
                box-shadow: none;
            }
            .session-tab-close {
                min-width: 24px;
                min-height: 24px;
                padding: 0;
            }
            .session-tab-close:hover {
                background-color: alpha(@theme_fg_color, 0.12);
            }
        """)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tab_strip = Gtk.Box(spacing=3)
        self.tab_strip.get_style_context().add_class("workspace-tabs")
        self.tab_scroll = Gtk.ScrolledWindow()
        self.tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.tab_scroll.add(self.tab_strip)
        self.panel.pack_start(self.tab_scroll, False, False, 0)
        self.panel.pack_start(self, True, True, 0)
        self.add_button = button("+", lambda _: new_session(), "新会话 · Ctrl+Shift+T")
        self.add_button.set_relief(Gtk.ReliefStyle.NONE)
        self.add_button.set_focus_on_click(False)
        self.add_button.get_accessible().set_name("新会话")
        self.tab_strip.pack_start(self.add_button, False, False, 0)
        self.drag_target = Gtk.TargetEntry.new(
            f"application/x-zterm-tab-{id(self)}", Gtk.TargetFlags.SAME_APP, 0)
        self.connect("switch-page", self.page_selected)
        self.connect("page-removed", self.page_removed)
        self.connect("page-reordered", self.page_reordered)
        self.tab_strip.connect("size-allocate", lambda *_: self.reveal_current())
        self.tab_scroll.get_hadjustment().connect("changed", lambda *_: self.reveal_current())

    def add_tab(self, session, close_session):
        row = Gtk.Box()
        row.set_size_request(160, -1)
        select = Gtk.ToggleButton()
        select.set_relief(Gtk.ReliefStyle.NONE)
        select.set_focus_on_click(False)
        select.add(session.tab_label)
        select.connect("clicked", self.select_clicked, session)
        close = button("×", lambda _: close_session(session), "关闭会话")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_focus_on_click(False)
        close.set_margin_start(4)
        close.set_margin_end(5)
        close.set_margin_top(4)
        close.set_margin_bottom(4)
        for widget, css_class in ((row, "session-tab"), (select, "session-tab-select"),
                                  (close, "session-tab-close")):
            style = widget.get_style_context()
            style.add_class(css_class)
            style.add_provider(self.tab_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        row.pack_start(select, True, True, 0)
        row.pack_end(close, False, False, 0)
        self.tabs[session] = (row, select)
        self.tab_strip.pack_start(row, False, False, 0)
        self.tab_strip.reorder_child(self.add_button, -1)
        select.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [self.drag_target], Gdk.DragAction.MOVE)
        select.drag_dest_set(Gtk.DestDefaults.ALL, [self.drag_target], Gdk.DragAction.MOVE)
        select.connect("drag-data-get", lambda widget, context, data, info, timestamp:
                       data.set(data.get_target(), 8, str(session.number).encode()))
        select.connect("drag-data-received", self.tab_dropped, session)
        row.show_all()
        self.sync_selection(self.get_nth_page(self.get_current_page()))

    def select_session(self, session):
        self.set_current_page(self.page_num(session))
        self.sync_selection(session)
        session.terminal.grab_focus()

    def sync_selection(self, current):
        for session, (row, select) in self.tabs.items():
            active = session is current
            style = row.get_style_context()
            if active:
                style.add_class("active")
            else:
                style.remove_class("active")
            # set_active emits clicked, so block our selection handler while syncing.
            if select.get_active() != active:
                select.handler_block_by_func(self.select_clicked)
                select.set_active(active)
                select.handler_unblock_by_func(self.select_clicked)

    def select_clicked(self, widget, session):
        self.select_session(session)

    def page_selected(self, notebook, page, index):
        self.sync_selection(page)
        GLib.idle_add(self.reveal_current)
        GLib.idle_add(page.terminal.grab_focus)

    def reveal_current(self):
        current = self.get_nth_page(self.get_current_page())
        if current not in self.tabs:
            return False
        row = self.tabs[current][0]
        x, _ = row.translate_coordinates(self.tab_strip, 0, 0)
        end = x + row.get_allocated_width()
        if self.page_num(current) == self.get_n_pages() - 1:
            add_x, _ = self.add_button.translate_coordinates(self.tab_strip, 0, 0)
            end = add_x + self.add_button.get_allocated_width()
        self.tab_scroll.get_hadjustment().clamp_page(x, end)
        return False

    def page_removed(self, notebook, session, index):
        row, select = self.tabs.pop(session)
        row.destroy()

    def page_reordered(self, notebook, session, index):
        self.tab_strip.reorder_child(self.tabs[session][0], index)
        self.tab_strip.reorder_child(self.add_button, -1)
        self.reveal_current()

    def tab_dropped(self, widget, context, x, y, data, info, timestamp, target):
        number = bytes(data.get_data()).decode("ascii")
        source = next((session for session in self.tabs if str(session.number) == number), None)
        if source is not None:
            self.reorder_child(source, self.page_num(target))
        Gtk.drag_finish(context, source is not None, False, timestamp)


class Window(Gtk.ApplicationWindow):
    def __init__(self, application=None, store=None, command_factory=None, enable_quota=True):
        super().__init__(application=application, title="zTerm")
        self.set_default_size(1120, 720)
        self.set_wmclass("ssh-workbench", "SSH Workbench")
        self.set_icon_from_file(str(Path(__file__).with_name("workbench.svg")))
        self.store = store or AliasStore()
        self.appearance_store = AppearanceStore(self.store.path.with_name("appearance.json"))
        self.appearance_error = None
        try:
            self.appearance = self.appearance_store.load()
        except (OSError, ValueError) as error:
            self.appearance = default_appearance()
            self.appearance_error = str(error)
        self.interface_style = InterfaceStyle(self)
        self.interface_style.apply(self.appearance)
        self.command_factory = command_factory or self.default_command
        self.groups, self.rows, self.counters = {}, {}, {}
        self.current = None
        self.quota_enabled = enable_quota
        self.quota_cache = {}
        self.quota_busy = False
        self.quota_closed = False
        self.load_error = None
        try:
            self.aliases = self.store.load()
        except (OSError, ValueError) as error:
            self.aliases = []
            self.load_error = str(error)
        header = Gtk.HeaderBar(title="zTerm")
        self.header = header
        header.set_show_close_button(True)
        self.sidebar_toggle = Gtk.ToggleButton(active=True)
        self.sidebar_toggle.set_image(Gtk.Image.new_from_icon_name("view-sidebar-symbolic", Gtk.IconSize.BUTTON))
        self.sidebar_toggle.set_tooltip_text("收起服务器栏")
        self.sidebar_toggle.get_accessible().set_name("展开或收起服务器栏")
        header.pack_start(self.sidebar_toggle)
        self.settings_button = Gtk.MenuButton(label="设置")
        self.settings_menu = Gtk.Menu()
        self.interface_style.attach(self.settings_menu)
        for label, callback in [("添加服务器", lambda _: self.add_dialog()),
                                ("导入别名", lambda _: self.import_aliases()),
                                ("快捷键", lambda _: self.show_shortcuts()),
                                ("外观设置", lambda _: AppearanceDialog(self)),
                                ("订阅额度", lambda _: self.quota_settings())]:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            self.settings_menu.append(item)
        self.settings_menu.show_all()
        self.settings_button.set_popup(self.settings_menu)
        header.pack_end(self.settings_button)
        self.set_titlebar(header)
        pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.pane = pane
        self.sidebar_width = 220
        pane.set_position(220)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(layout)
        layout.pack_start(pane, True, True, 0)
        self.status_bar = Gtk.Box(spacing=8, margin=6)
        self.status_bar.get_style_context().add_class("workspace-status")
        connection = Gtk.Box(spacing=4)
        self.connection_label = Gtk.Label(label="未选择服务器", xalign=0)
        self.connection_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.connection_label.set_max_width_chars(24)
        connection.pack_start(self.connection_label, False, False, 0)
        self.reconnect = button("重新连接", lambda _: self.reconnect_current())
        self.reconnect.set_relief(Gtk.ReliefStyle.NONE)
        self.reconnect.set_no_show_all(True)
        connection.pack_start(self.reconnect, False, False, 0)
        self.status_bar.pack_start(connection, False, False, 0)
        self.quota_label = Gtk.Label(label="选择服务器后显示 Codex 订阅额度")
        self.quota_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.quota_label.set_max_width_chars(55)
        self.quota_label.set_justify(Gtk.Justification.CENTER)
        self.status_bar.set_center_widget(self.quota_label)
        self.quota_refresh = button("↻", lambda _: self.refresh_quota(force=True), "刷新当前服务器订阅额度")
        self.quota_refresh.set_relief(Gtk.ReliefStyle.NONE)
        self.status_bar.pack_end(self.quota_refresh, False, False, 0)
        layout.pack_end(self.status_bar, False, False, 0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        self.sidebar = side
        side.get_style_context().add_class("workspace-sidebar")
        side.set_size_request(170, -1)
        side.pack_start(Gtk.Label(label="服务器", xalign=0), False, False, 4)
        self.search = Gtk.SearchEntry(placeholder_text="搜索 SSH 别名")
        self.search.connect("search-changed", lambda _: self.server_list.invalidate_filter())
        side.pack_start(self.search, False, False, 0)
        self.server_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.server_list.set_filter_func(lambda row: not row.alias or
                                        self.search.get_text().lower() in row.alias.lower())
        self.server_list.connect("row-selected", self.selected)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.server_list)
        side.pack_start(scroll, True, True, 0)
        self.remove_button = button("移除服务器", lambda _: self.remove_alias())
        self.remove_button.set_sensitive(False)
        side.pack_end(self.remove_button, False, False, 0)
        pane.pack1(side, False, False)
        self.stack = Gtk.Stack()
        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        welcome.set_valign(Gtk.Align.CENTER)
        welcome.set_halign(Gtk.Align.CENTER)
        welcome.pack_start(Gtk.Image.new_from_pixbuf(
            self.get_icon().scale_simple(64, 64, GdkPixbuf.InterpType.BILINEAR)), False, False, 0)
        welcome.pack_start(Gtk.Label(label="你的服务器，一个工作台"), False, False, 0)
        welcome.pack_start(Gtk.Label(label="在右上角“设置”中添加服务器或导入 SSH 别名。\n"
                                    "选择服务器后，用最后一个标签后的 ＋ 打开更多会话。\n"
                                    "切换服务器会保留当前连接。", justify=Gtk.Justification.CENTER), False, False, 0)
        self.stack.add_named(welcome, "welcome")
        pane.pack2(self.stack, True, False)
        self.add_row("")
        for alias in self.aliases:
            self.add_row(alias)
        self.connect("key-press-event", self.key_pressed)
        self.connect("delete-event", self.close_window)
        self.show_all()
        side.set_no_show_all(True)
        self.sidebar_toggle.connect("toggled", self.toggle_sidebar)
        self.server_list.unselect_all()
        self.stack.set_visible_child_name("welcome")
        self.connect("destroy", self.stop_quota)
        self.quota_timer = GLib.timeout_add_seconds(30, self.quota_tick) if enable_quota else None
        if self.load_error:
            GLib.idle_add(self.info, "读取配置失败", self.load_error)
        if self.appearance_error:
            GLib.idle_add(self.info, "读取外观配置失败", self.appearance_error)

    def toggle_sidebar(self, toggle):
        visible = toggle.get_active()
        if not visible:
            self.sidebar_width = self.pane.get_position()
        self.sidebar.set_visible(visible)
        if visible:
            self.pane.set_position(self.sidebar_width)
        toggle.set_tooltip_text("收起服务器栏" if visible else "展开服务器栏")

    def stop_quota(self, *_):
        self.quota_closed = True
        if self.quota_timer is not None:
            GLib.source_remove(self.quota_timer)
            self.quota_timer = None

    def current_session(self):
        notebook = self.groups.get(self.current)
        return notebook.get_nth_page(notebook.get_current_page()) if notebook else None

    def render_status(self, session=None):
        session = session or self.current_session()
        server = self.current or "本地终端"
        if session is None:
            text = "未选择服务器" if self.current is None else server + " · 无会话"
            detail = None
        else:
            state = "正在连接…" if session.pending else "在线" if session.alive else ""
            text = server + " · " + state
            detail = session.status_detail
        self.connection_label.set_text(text)
        self.connection_label.set_tooltip_text(detail or text)
        self.reconnect.set_label("重新连接" if self.current else "重新打开")
        self.reconnect.set_tooltip_text(detail)
        self.reconnect.set_visible(session is not None and not session.active)

    def reconnect_current(self):
        session = self.current_session()
        if session is not None:
            session.start()

    def session_switched(self, notebook, page, index, alias):
        if alias == self.current:
            self.render_status(page)

    def quota_tick(self):
        self.render_quota()
        self.refresh_quota()
        return True

    def render_quota(self):
        if self.current is None:
            self.quota_label.set_text("选择服务器后显示 Codex 订阅额度")
            self.quota_label.set_tooltip_text(None)
            return
        record = self.quota_cache.get(self.current)
        server = self.current or "本地终端"
        if record is None:
            text = "Codex · 正在查询 " + server
        elif "error" in record:
            text = "Codex · " + record["error"]
        else:
            text = display_quota("Codex", record["codex"])
        self.quota_label.set_text(text)
        details = [server, text, "使用比例；重置时间按本机时区显示。每 5 分钟自动查询，也可点击 ↻。"]
        if record:
            observed = record.get("codex", {}).get("observed")
            if observed:
                details.append("数据时间：" + time.strftime("%m-%d %H:%M:%S", time.localtime(observed)))
        self.quota_label.set_tooltip_text("\n".join(details))

    def refresh_quota(self, force=False):
        if not self.quota_enabled or self.current is None or self.quota_busy or self.quota_closed:
            return
        alias = self.current
        record = self.quota_cache.get(alias)
        if not force and record and time.time() - record["fetched"] < 300:
            return
        self.quota_busy = True
        self.quota_refresh.set_sensitive(False)

        def work():
            result = fetch_quota(alias)
            if not self.quota_closed:
                GLib.idle_add(self.quota_received, alias, result)

        threading.Thread(target=work, daemon=True).start()

    def quota_received(self, alias, result):
        if self.quota_closed:
            return False
        self.quota_busy = False
        self.quota_refresh.set_sensitive(True)
        result["fetched"] = time.time()
        self.quota_cache[alias] = result
        self.render_quota()
        self.refresh_quota()
        return False

    def quota_settings(self):
        self.info("Codex 订阅额度", "底栏显示当前所选机器上的 Codex 订阅使用比例和重置时间。\n"
                  "请在该机器安装 codex CLI，并使用订阅账号登录。\n"
                  "远端查询需要 Python 3.9+ 和免交互 SSH 登录（如密钥或 agent）。\n"
                  "每 5 分钟自动刷新，也可点击底栏右侧 ↻。\n"
                  "重置时间按本机时区显示，悬停可查看数据时间。")

    def show_shortcuts(self):
        self.info("快捷键", "Ctrl+Shift+T：新会话\nCtrl+Shift+W：关闭会话\n"
                  "Ctrl+Shift+C / V：复制 / 粘贴\nAlt+1 … 9：切换会话\n"
                  "Ctrl+PageUp / PageDown：切换会话\nCtrl+加号 / 减号 / 0：字体缩放")

    def set_appearance(self, settings):
        self.appearance = settings.copy()
        self.interface_style.apply(settings)
        for group in self.groups.values():
            for session in group.get_children():
                apply_appearance(session.terminal, settings)
                session.terminal.set_font_scale(1.0)

    @staticmethod
    def default_command(alias):
        return ssh_command(alias) if alias else [pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash", "-l"]

    def info(self, title, message):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(message)
        self.interface_style.attach(dialog)
        dialog.run()
        dialog.destroy()
        return False

    def confirm(self, title, message):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=title)
        dialog.format_secondary_text(message)
        self.interface_style.attach(dialog)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "关闭", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        answer = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return answer

    def persist(self, aliases):
        if self.load_error:
            self.info("配置不可写入", "请先修复现有配置并重新启动，避免覆盖原有数据。\n" + self.load_error)
            return False
        try:
            self.store.save(aliases)
        except OSError as error:
            self.info("保存失败", str(error))
            return False
        self.aliases = aliases
        return True

    def add_row(self, alias):
        row = Gtk.ListBoxRow()
        row.alias = alias
        label = Gtk.Label(label=alias or "本地终端", xalign=0, margin=12)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        row.add(label)
        row.set_tooltip_text("ssh " + alias if alias else "本地登录 shell")
        self.rows[alias] = row
        self.server_list.add(row)
        row.show_all()

    def add_dialog(self):
        dialog = Gtk.Dialog(title="添加 SSH 别名", transient_for=self, modal=True)
        self.interface_style.attach(dialog)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_border_width(16)
        content.add(Gtk.Label(label="输入 ~/.ssh/config 中的 Host 别名，例如 dev-server。\n连接将使用系统 SSH 配置中的用户、端口和密钥。", xalign=0))
        entry = Gtk.Entry(placeholder_text="SSH 别名", activates_default=True)
        content.add(entry)
        error_label = Gtk.Label(xalign=0)
        content.add(error_label)
        dialog.show_all()
        while dialog.run() == Gtk.ResponseType.OK:
            alias = entry.get_text().strip()
            if not valid_alias(alias):
                error_label.set_text("请使用字母、数字、下划线、点或连字符，且不要以连字符开头。")
                continue
            if alias in self.aliases:
                error_label.set_text("该别名已存在。")
                continue
            if self.persist(self.aliases + [alias]):
                self.add_row(alias)
                break
        dialog.destroy()

    def import_aliases(self):
        try:
            found = discover_aliases()
        except (OSError, UnicodeError) as error:
            self.info("导入失败", str(error))
            return
        added = [alias for alias in found if alias not in self.aliases]
        if not added:
            self.info("没有新别名", "未找到可导入的新 Host 别名；通配符不会导入。也可以点击“添加服务器”手动保存。")
            return
        if self.persist(self.aliases + added):
            for alias in added:
                self.add_row(alias)
            self.info("导入完成", f"已添加 {len(added)} 个 SSH 别名。点击左侧服务器进行连接。")

    def selected(self, widget, row):
        if row is None:
            return
        self.current = row.alias
        self.update_server_title()
        self.render_quota()
        self.refresh_quota()
        self.remove_button.set_sensitive(bool(row.alias))
        if row.alias not in self.groups:
            notebook = SessionNotebook(self.new_session)
            notebook.connect_after("switch-page", self.session_switched, row.alias)
            notebook.connect_after("page-removed", lambda *_: self.render_status())
            self.groups[row.alias] = notebook
            self.counters[row.alias] = 0
            self.stack.add_named(notebook.panel, "server:" + row.alias)
            notebook.panel.show_all()
        notebook = self.groups[row.alias]
        self.stack.set_visible_child(notebook.panel)
        if notebook.get_n_pages() == 0:
            self.new_session()
        else:
            notebook.get_nth_page(notebook.get_current_page()).terminal.grab_focus()
        self.render_status()

    def new_session(self):
        if self.current is None:
            return
        alias = self.current
        self.counters[alias] += 1
        session = Session(self, alias, self.counters[alias], self.command_factory(alias))
        notebook = self.groups[alias]
        index = notebook.append_page(session, None)
        notebook.add_tab(session, self.close_session)
        notebook.set_current_page(index)
        self.render_status()
        session.terminal.grab_focus()
        return session

    def update_server_title(self):
        title = "zTerm" if self.current is None else self.current or "本地终端"
        self.header.set_title(title)
        self.set_title(title)

    def close_session(self, session):
        if session.active and not self.confirm("关闭会话？", "此会话仍在运行。关闭会终止连接及终端内的前台任务。"):
            return
        session.dispose()

    def remove_alias(self):
        alias = self.current
        if not alias:
            return
        notebook = self.groups.get(alias)
        sessions = notebook.get_children() if notebook else []
        if any(session.active for session in sessions) and not self.confirm(
                "移除服务器并关闭会话？", f"{alias} 的所有活动会话都会关闭。"):
            return
        if not self.persist([item for item in self.aliases if item != alias]):
            return
        # Deselect before removing the row: GTK may otherwise select a neighbour.
        self.server_list.unselect_all()
        self.current = None
        self.rows.pop(alias).destroy()
        if notebook:
            for session in sessions:
                session.dispose()
            notebook.panel.destroy()
            del self.groups[alias]
        self.stack.set_visible_child_name("welcome")
        self.remove_button.set_sensitive(False)
        self.update_server_title()
        self.render_quota()
        self.render_status()

    def close_window(self, *_):
        sessions = [session for group in self.groups.values() for session in group.get_children()]
        count = sum(session.active for session in sessions)
        if count and not self.confirm("关闭 zTerm？", f"还有 {count} 个活动会话，关闭窗口将终止这些会话。"):
            return True
        for session in sessions:
            session.dispose()
        return False

    def key_pressed(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)
        key = Gdk.keyval_name(event.keyval).lower()
        notebook = self.groups.get(self.current)
        session = notebook.get_nth_page(notebook.get_current_page()) if notebook else None
        if ctrl and shift and key == "t":
            self.new_session()
            return True
        if session is None:
            return False
        if ctrl and shift and key in ("w", "c", "v"):
            if key == "w":
                self.close_session(session)
            elif key == "c":
                session.terminal.copy_clipboard_format(Vte.Format.TEXT)
            else:
                session.terminal.paste_clipboard()
            return True
        if alt and key in "123456789" and len(key) == 1:
            if int(key) <= notebook.get_n_pages():
                notebook.set_current_page(int(key) - 1)
            return True
        if ctrl and key in ("page_up", "page_down"):
            delta = 1 if key == "page_down" else -1
            notebook.set_current_page((notebook.get_current_page() + delta) % notebook.get_n_pages())
            return True
        if ctrl and key in ("plus", "equal", "minus", "0"):
            scale = session.terminal.get_font_scale()
            scale = 1.0 if key == "0" else max(0.5, min(2.5, scale + (-0.1 if key == "minus" else 0.1)))
            session.terminal.set_font_scale(scale)
            return True
        return False


class Application(Gtk.Application):
    def __init__(self):
        GLib.set_application_name("zTerm")
        super().__init__(application_id="local.ssh.Workbench", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = Window(self)
        window.present()


if __name__ == "__main__":
    sys.exit(Application().run(sys.argv))
