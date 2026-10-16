"""Saved SSH aliases. Connection options remain owned by OpenSSH."""

import glob
import json
import os
from pathlib import Path
import re
import shlex
import tempfile


def valid_alias(alias):
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", alias))


def discover_aliases(config=None):
    """Discover literal Host names, including files referenced by Include.

    This is a name index only; OpenSSH evaluates all connection options.
    """
    config = Path(config or Path.home() / ".ssh/config")
    aliases, visited = [], set()

    def visit(path):
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            return
        for line in lines:
            # OpenSSH accepts both "Host foo" and "Host=foo".
            line = re.sub(r"^(\s*[A-Za-z]+)\s*=\s*", r"\1 ", line)
            try:
                parts = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not parts:
                continue
            if parts[0].lower() == "host":
                for alias in parts[1:]:
                    if valid_alias(alias) and alias not in aliases:
                        aliases.append(alias)
            elif parts[0].lower() == "include":
                for pattern in parts[1:]:
                    pattern = Path(pattern).expanduser()
                    if not pattern.is_absolute():
                        pattern = config.parent / pattern
                    for match in sorted(glob.glob(str(pattern))):
                        visit(Path(match))

    visit(config)
    return aliases


class AliasStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "ssh-workbench/servers.json"

    def load(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text())
        aliases = data.get("aliases") if isinstance(data, dict) else None
        if (not isinstance(aliases, list)
                or any(not isinstance(a, str) or not valid_alias(a) for a in aliases)
                or len(set(aliases)) != len(aliases)):
            raise ValueError("别名配置格式错误；请检查 " + str(self.path))
        return aliases

    def save(self, aliases):
        save_json(self.path, {"aliases": aliases})


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# GNOME Terminal 3.52 schema defaults as shipped in Ubuntu 24.04.
GNOME_PALETTE = ["#171421", "#c01c28", "#26a269", "#a2734c", "#12488b", "#a347ba", "#2aa1b3", "#d0cfcc",
                 "#5e5c64", "#f66151", "#33da7a", "#e9ad0c", "#2a7bde", "#c061cb", "#33c7de", "#ffffff"]

# Removed presets are only recognized for migration, never offered in the UI.
LEGACY_THEME_MIGRATIONS = {
    "classic": ("ubuntu-default", ("#eeeeec", "#241f31", "#eeeeec")),
    "dark": ("gnome-gray-on-black", ("#e5e7eb", "#15181e", "#93c5fd")),
    "light": ("gnome-black-on-white", ("#24292f", "#fafafa", "#0969da")),
    "ocean": ("ubuntu-default", ("#d8e7f3", "#102332", "#7dd3fc")),
}

# RGB sources and ANSI adaptation details are recorded in THEME_SOURCES.txt.
# Solarized follows the local Vim variant, including its experimental green.
THEMES = {
    **{key: {"name": name, "foreground": fg, "background": bg, "cursor": fg,
             "palette": GNOME_PALETTE.copy()}
       for key, name, fg, bg in (
           ("gnome-black-on-light-yellow", "浅黄背景黑字", "#000000", "#ffffdd"),
           ("gnome-black-on-white", "白底黑字", "#000000", "#ffffff"),
           ("gnome-gray-on-black", "黑底灰字", "#aaaaaa", "#000000"),
           ("gnome-white-on-black", "黑底白字", "#ffffff", "#000000"),
           ("gnome-green-on-black", "黑底绿字", "#00ff00", "#000000"),
           ("ubuntu-default", "Ubuntu 默认（李子色）", "#ffffff", "#300a24"),
       )},
    "solarized-light": {
        "name": "Solarized Light", "foreground": "#657b83", "background": "#fdf6e3", "cursor": "#657b83",
        "palette": ["#eee8d5", "#dc322f", "#719e07", "#b58900", "#268bd2", "#d33682", "#2aa198", "#073642",
                    "#fdf6e3", "#cb4b16", "#93a1a1", "#839496", "#657b83", "#6c71c4", "#586e75", "#002b36"],
    },
    "solarized-dark": {
        "name": "Solarized Dark", "foreground": "#839496", "background": "#002b36", "cursor": "#839496",
        "palette": ["#073642", "#dc322f", "#719e07", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
    "gruvbox-light": {
        "name": "Gruvbox Light", "foreground": "#3c3836", "background": "#fbf1c7", "cursor": "#3c3836",
        "palette": ["#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64",
                    "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836"],
    },
    "gruvbox-dark": {
        "name": "Gruvbox Dark", "foreground": "#ebdbb2", "background": "#282828", "cursor": "#ebdbb2",
        "palette": ["#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                    "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"],
    },
    "papercolor-light": {
        "name": "PaperColor Light", "foreground": "#444444", "background": "#eeeeee", "cursor": "#005f87",
        "palette": ["#eeeeee", "#af0000", "#008700", "#5f8700", "#0087af", "#878787", "#005f87", "#444444",
                    "#bcbcbc", "#d70000", "#d70087", "#8700af", "#d75f00", "#d75f00", "#005faf", "#005f87"],
    },
    "papercolor-dark": {
        "name": "PaperColor Dark", "foreground": "#d0d0d0", "background": "#1c1c1c", "cursor": "#c6c6c6",
        "palette": ["#1c1c1c", "#af005f", "#5faf00", "#d7af5f", "#5fafd7", "#808080", "#d7875f", "#d0d0d0",
                    "#585858", "#5faf5f", "#afd700", "#af87d7", "#ffaf00", "#ff5faf", "#00afaf", "#5f8787"],
    },
    "peaksea-light": {
        "name": "Peaksea Light", "foreground": "#000000", "background": "#e0e0e0", "cursor": "#008000",
        "palette": ["#e0e0e0", "#c03000", "#009030", "#907000", "#007068", "#a030a0", "#2060a8", "#000000",
                    "#686868", "#c03000", "#009030", "#907000", "#007068", "#a030a0", "#2060a8", "#000000"],
    },
    "peaksea-dark": {
        "name": "Peaksea Dark", "foreground": "#d0d0d0", "background": "#202020", "cursor": "#00f000",
        "palette": ["#202020", "#f08060", "#60f080", "#e0c060", "#80c0e0", "#f0c0f0", "#c0d8f8", "#d0d0d0",
                    "#b0b0b0", "#f08060", "#60f080", "#e0c060", "#80c0e0", "#f0c0f0", "#c0d8f8", "#d0d0d0"],
    },
}


INTERFACE_THEMES = {"follow-terminal": "跟随终端配色（推荐）", "system": "跟随系统外观",
                    **{key: theme["name"] for key, theme in THEMES.items()}}


def blend_colors(background, foreground, amount):
    return "#" + "".join(f"{round(bg + (fg - bg) * amount):02x}"
                         for bg, fg in zip(bytes.fromhex(background[1:]), bytes.fromhex(foreground[1:])))


def interface_colors(settings):
    name = settings.get("interface_theme", "follow-terminal")
    if name == "system":
        return None
    if name == "follow-terminal":
        name = settings["theme"]
    theme = THEMES[name]
    background = theme["background"]
    foreground = "#d0d0d0" if name == "gnome-green-on-black" else theme["foreground"]
    accent = ("#e95420" if name == "ubuntu-default" else
              "#00af00" if name == "gnome-green-on-black" else theme["palette"][4])
    return {"background": background, "foreground": foreground, "accent": accent,
            "surface": blend_colors(background, foreground, 0.06),
            "hover": blend_colors(background, foreground, 0.12),
            "border": blend_colors(background, foreground, 0.25),
            "selection": blend_colors(background, accent, 0.25)}


def default_appearance():
    return {"font": "Monospace 11", "theme": "ubuntu-default", "interface_theme": "follow-terminal",
            **{key: THEMES["ubuntu-default"][key] for key in ("foreground", "background", "cursor")}}


class AppearanceStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "ssh-workbench/appearance.json"

    def validate(self, data):
        if (not isinstance(data, dict)
                or not isinstance(data.get("font"), str) or not data["font"].strip()
                or not isinstance(data.get("theme"), str)
                or data["theme"] not in THEMES.keys() | LEGACY_THEME_MIGRATIONS.keys()
                or not isinstance(data.get("interface_theme", "follow-terminal"), str)
                or data.get("interface_theme", "follow-terminal") not in INTERFACE_THEMES
                or any(not isinstance(data.get(key), str)
                       or not re.fullmatch(r"#[0-9a-fA-F]{6}", data[key])
                       for key in ("foreground", "background", "cursor"))):
            raise ValueError("外观配置格式错误；请检查 " + str(self.path))
        if data["theme"] in LEGACY_THEME_MIGRATIONS:
            theme, previous_colors = LEGACY_THEME_MIGRATIONS[data["theme"]]
            data = dict(data, theme=theme)
            for key, previous in zip(("foreground", "background", "cursor"), previous_colors):
                if data[key].lower() == previous:
                    data[key] = THEMES[theme][key]
        return dict(data, interface_theme=data.get("interface_theme", "follow-terminal"))

    def load(self):
        if not self.path.exists():
            return default_appearance()
        return self.validate(json.loads(self.path.read_text()))

    def save(self, data):
        save_json(self.path, self.validate(data))


def ssh_command(alias):
    if not valid_alias(alias):
        raise ValueError("无效的 SSH 别名")
    return ["/usr/bin/ssh", alias]
