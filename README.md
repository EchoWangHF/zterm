# term — SSH 工作台

基于 Python、GTK 3 和 VTE 的独立终端应用，支持本地终端与按服务器分组的 SSH 会话。

- 保存或导入 SSH 别名，连接选项由系统 OpenSSH 和 `~/.ssh/config` 管理。
- 每个服务器拥有独立会话标签，切换服务器保留连接；支持重连及关闭确认。
- 设置字体、字号、文字、背景及光标颜色，预览后保存并应用到所有会话。
- Solarized、Gruvbox、PaperColor、Peaksea 的 Light / Dark 配色及 ANSI 16 色调色板。
- 独立应用图标和桌面启动入口；配置在重启后保留，不自动重连服务器。

## 安装与运行

适用于提供 GTK 3 / VTE 的 Linux 桌面。已在 Ubuntu 24.04、Python 3.12、VTE 0.76 上验证。

Ubuntu / Debian 依赖：

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 librsvg2-common openssh-client
sh install.sh
~/.local/bin/ssh-workbench
```

也可在应用菜单搜索“SSH 工作台”或“SSH Workbench”。直接从源码运行：

```sh
/usr/bin/python3 app.py
```

安装文件位于 `${XDG_DATA_HOME:-~/.local/share}/ssh-workbench/`，设置位于
`${XDG_CONFIG_HOME:-~/.config}/ssh-workbench/`。更新安装后，重新打开应用以加载新版本。

## 验证

测试需要 `xvfb` 和 `xauth`，使用临时配置与本地模拟进程，不连接真实 SSH 服务器。

```sh
/usr/bin/python3 -m py_compile app.py model.py test_workbench.py
GDK_BACKEND=x11 xvfb-run -a /usr/bin/python3 test_workbench.py
```

测试涵盖配置保存、SSH 参数、PTY 输入输出、会话切换和回收、外观保存恢复，
以及八个命名主题的实际 ANSI 色块渲染。截图是测试生成物，不纳入版本控制。

详细用法和快捷键见 [HELP.txt](HELP.txt)，主题来源与适配约定见
[THEME_SOURCES.txt](THEME_SOURCES.txt)。主题只改变终端默认色及 ANSI 0–15，
不覆盖程序自行指定的 truecolor 颜色。
