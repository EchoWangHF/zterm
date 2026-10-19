#!/bin/sh
# Build an architecture-independent package without root privileges.
set -eu
umask 022
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=${1:-0.2.3}
dpkg --validate-version "$version"
case "$version" in
    *[!0-9A-Za-z.+:~\-]*) echo 'Invalid package version' >&2; exit 1 ;;
esac
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
chmod 755 "$stage"
app_dir="$stage/usr/share/ssh-workbench"
doc_dir="$stage/usr/share/doc/zterm"
install -d "$stage/DEBIAN" "$stage/usr/bin" "$app_dir" "$doc_dir" \
    "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps"
install -m 644 "$source_dir/app.py" "$source_dir/model.py" "$source_dir/quota.py" "$source_dir/workbench.svg" "$app_dir/"
install -m 644 "$source_dir/README.md" "$source_dir/HELP.txt" "$source_dir/THEME_SOURCES.txt" \
    "$source_dir/LICENSE" "$source_dir/THIRD_PARTY_NOTICES.txt" "$doc_dir/"
cat "$source_dir/THIRD_PARTY_NOTICES.txt" "$source_dir/LICENSE" > "$doc_dir/copyright"
install -m 644 "$source_dir/workbench.svg" \
    "$stage/usr/share/icons/hicolor/scalable/apps/local.ssh.Workbench.svg"
cat > "$stage/usr/bin/ssh-workbench" <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 /usr/share/ssh-workbench/app.py "$@"
LAUNCHER
chmod 755 "$stage/usr/bin/ssh-workbench"
install -m 755 "$stage/usr/bin/ssh-workbench" "$stage/usr/bin/zterm"
cat > "$stage/usr/share/applications/local.ssh.Workbench.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=zTerm
Name[zh_CN]=zTerm
Comment=SSH servers and per-server terminal sessions
Exec=/usr/bin/zterm
Icon=local.ssh.Workbench
Terminal=false
Categories=System;TerminalEmulator;
StartupNotify=true
StartupWMClass=ssh-workbench
DESKTOP
cat > "$stage/DEBIAN/control" <<CONTROL
Package: zterm
Version: $version
Section: utils
Priority: optional
Architecture: all
Provides: ssh-workbench (= $version)
Conflicts: ssh-workbench
Replaces: ssh-workbench
Maintainer: zhouyawei <185499624@qq.com>
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-3.0, gir1.2-vte-2.91, librsvg2-common, openssh-client
Homepage: https://github.com/zhou411X/zterm
Description: zTerm terminal with grouped SSH sessions
 Independent local and SSH terminal with a collapsible server sidebar,
 per-server session tabs, configurable fonts and fourteen named color themes.
CONTROL
# dpkg records root ownership; the staging directory remains user-owned.
install -d "$source_dir/dist"
dpkg-deb --root-owner-group --build "$stage" "$source_dir/dist/zterm_${version}_all.deb"
