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


LEGACY_THEMES = {
    "classic": {"name": "经典紫", "foreground": "#eeeeec", "background": "#241f31",
                "cursor": "#eeeeec"},
    "dark": {"name": "深色", "foreground": "#e5e7eb", "background": "#15181e",
             "cursor": "#93c5fd"},
    "light": {"name": "浅色", "foreground": "#24292f", "background": "#fafafa",
              "cursor": "#0969da"},
    "ocean": {"name": "深海蓝", "foreground": "#d8e7f3", "background": "#102332",
              "cursor": "#7dd3fc"},
}

DARK_PALETTE = ["#2e3436", "#ef5350", "#8bc34a", "#fdd835", "#64b5f6", "#ce93d8", "#4dd0e1", "#d3d7cf",
                "#888a85", "#ff8a80", "#b9f6a5", "#ffff8d", "#90caf9", "#ea80fc", "#84ffff", "#ffffff"]
LIGHT_PALETTE = ["#24292f", "#b42318", "#27632a", "#805700", "#1755ad", "#8039a4", "#006b75", "#6e7781",
                 "#57606a", "#cf222e", "#1a7f37", "#9a6700", "#0969da", "#8250df", "#087e8b", "#8c959f"]

# RGB sources and ANSI adaptation details are recorded in THEME_SOURCES.txt.
# Solarized follows the local Vim variant, including its experimental green.
THEMES = {
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
    **{key: {**theme, "palette": LIGHT_PALETTE if key == "light" else DARK_PALETTE}
       for key, theme in LEGACY_THEMES.items()},
}


def default_appearance():
    return {"font": "Monospace 11", "theme": "classic",
            **{key: THEMES["classic"][key] for key in ("foreground", "background", "cursor")}}


class AppearanceStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "ssh-workbench/appearance.json"

    def validate(self, data):
        if (not isinstance(data, dict)
                or not isinstance(data.get("font"), str) or not data["font"].strip()
                or not isinstance(data.get("theme"), str) or data["theme"] not in THEMES
                or any(not isinstance(data.get(key), str)
                       or not re.fullmatch(r"#[0-9a-fA-F]{6}", data[key])
                       for key in ("foreground", "background", "cursor"))):
            raise ValueError("外观配置格式错误；请检查 " + str(self.path))
        return data

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
