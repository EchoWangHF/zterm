#!/bin/sh
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data_dir/ssh-workbench"
bin_dir="$HOME/.local/bin"
icon_dir="$data_dir/icons/hicolor"
install -d "$app_dir" "$data_dir/applications" "$bin_dir" "$icon_dir/scalable/apps"
install -m 644 "$source_dir/app.py" "$source_dir/model.py" "$source_dir/quota.py" \
    "$source_dir/HELP.txt" "$source_dir/THEME_SOURCES.txt" "$source_dir/workbench.svg" \
    "$source_dir/LICENSE" "$source_dir/THIRD_PARTY_NOTICES.txt" "$app_dir/"
install -m 644 "$source_dir/workbench.svg" "$icon_dir/scalable/apps/local.ssh.Workbench.svg"
install -m 755 "$source_dir/credentials.py" "$app_dir/"
# The launcher resolves XDG_DATA_HOME at runtime, including paths with spaces.
cat > "$bin_dir/ssh-workbench" <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ssh-workbench/app.py" "$@"
LAUNCHER
chmod 755 "$bin_dir/ssh-workbench"
install -m 755 "$bin_dir/ssh-workbench" "$bin_dir/zterm"
cat > "$data_dir/applications/local.ssh.Workbench.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=zTerm
Name[zh_CN]=zTerm
Comment=SSH servers and per-server terminal sessions
Exec="$bin_dir/zterm"
Icon=local.ssh.Workbench
Terminal=false
Categories=System;TerminalEmulator;Network;
StartupNotify=true
StartupWMClass=ssh-workbench
DESKTOP
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$icon_dir"
fi
printf 'Installed: %s\n' "$bin_dir/zterm"
