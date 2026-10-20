# zterm

zterm 项目的应用显示名为 **zTerm**，是基于 Python、GTK 3 和 VTE 的独立终端应用，支持本地终端与按服务器分组的 SSH 会话。

[下载 deb 安装包](https://github.com/zhou411X/zterm/releases/latest) · [GPL-3.0 许可证](LICENSE)

- 添加服务器时填写显示别名、IP、用户名、密码和端口（默认 22）；侧栏和窗口标题显示别名。密码存入系统钥匙环，留空使用密钥登录。
- 左侧栏按钮或右键服务器 →“编辑服务器”可修改别名、IP、用户名、密码和端口；密码留空保留原密码，也可清除已保存密码。连接信息用于新会话或重新连接，现有连接继续运行。
- “复制服务器”按同一 IP/用户名/端口和已存密码新建副本（密码自动复制，无需重输），副本别名自动加“ 副本”后缀。
- 每个服务器拥有独立会话标签，切换服务器保留连接；支持重连及关闭确认。
- 左侧服务器支持拖拽排序，插入位置以指示线显示，顺序自动保存并在重启后保留；本地终端固定在顶部。
- 加宽会话标签将序号与右侧关闭按钮放在同一个标签内；新建“+”紧随最后一个标签，支持拖动排序和横向滚动。
- 标签编号始终按从左到右的位置更新，拖拽或关闭后连续排列；`Alt+1 … 9` 对应显示编号。
- 窗口标题随当前服务器切换；统一底栏左侧显示服务器与当前会话状态，离线后可直接重连，中间显示 Codex 订阅额度。
- 设置字体、字号、文字、背景及光标颜色，预览后保存并应用到所有会话。
- 应用界面主题覆盖标题栏、侧栏、标签、底栏和菜单；可跟随终端配色、独立搭配 14 套明暗配色，或跟随系统外观，支持预览、取消恢复及保存。
- Solarized、Gruvbox、PaperColor、Peaksea 的 Light / Dark 配色及 ANSI 16 色调色板。
- 六套 GNOME Terminal / Ubuntu 配色：浅黄背景黑字、白底黑字、黑底灰字、黑底白字、黑底绿字、Ubuntu 默认（李子色），共 14 套主题。
- 圆角羽毛应用图标和独立桌面启动入口；配置在重启后保留，不自动重连服务器。

## v0.2.5 更新

- 添加和编辑服务器使用独立窗口，可单独移动、缩放，主终端继续操作。
- 左侧服务器支持拖拽排序，顺序自动保存，已有连接保持运行。
- 右键服务器可修改别名、IP、用户名、密码和端口；旧条目自动读取原 SSH 连接信息。
- 编辑时密码留空保留原密码，可显式清除；更新失败时保留原配置和密码。
- 连接信息修改后用于新会话和重连，Codex 额度刷新为新连接对应账号。
- 羽毛图标改为透明圆角。

## 安装已发布的 deb 包（推荐）

适用于提供 GTK 3 / VTE 的 Linux 桌面。已在 Ubuntu 24.04、Python 3.12、VTE 0.76 上验证。
需要图形桌面环境；其他 Debian / Ubuntu 版本尚未逐一验证。

从 [Releases](https://github.com/zhou411X/zterm/releases) 下载
`zterm_0.2.5_all.deb`，进入下载目录后安装：

```sh
sudo apt install ./zterm_0.2.5_all.deb
/usr/bin/zterm
```

也支持 `dpkg -i`；如果提示缺少依赖，随后用 apt 补齐：

```sh
sudo dpkg -i ./zterm_0.2.5_all.deb
# 仅当上一步报告缺少依赖时执行：
sudo apt --fix-broken install
```

也可在应用菜单搜索“zTerm”。升级使用同样的安装命令，
之后重新打开应用；已有窗口不会自动加载新版。Release 同时提供 `SHA256SUMS`，
与 deb 放在同一目录后可运行 `sha256sum -c SHA256SUMS` 校验下载内容。

## 从源码运行与用户级安装

这是 Python 程序，不需要 C/C++ 编译器、CMake 或 Node.js。先获取源码并安装运行依赖：

```sh
sudo apt update
sudo apt install git python3 python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-secret-1 gnome-keyring librsvg2-common openssh-client
git clone https://github.com/zhou411X/zterm.git
cd zterm
/usr/bin/python3 app.py
```

程序要求 Python 3.9+。使用系统 `/usr/bin/python3`，以便找到 apt 安装的 GTK/VTE 绑定。
如需安装到当前用户（这一步无需 sudo）：

```sh
sh install.sh
~/.local/bin/zterm
```

用户级安装文件位于 `${XDG_DATA_HOME:-~/.local/share}/ssh-workbench/`，
设置位于 `${XDG_CONFIG_HOME:-~/.config}/ssh-workbench/`。服务器别名和外观分别保存在
`servers.json` 和 `appearance.json`。在左侧“添加服务器”按钮中指定 IPv4/IPv6 地址、用户名和端口，
显示别名留空时使用 IP；这些连接字段优先于 `~/.ssh/config`。密钥和跳板等其他设置仍由 OpenSSH 管理。
“添加服务器”使用独立窗口，可单独移动、缩放，填写期间主终端仍可操作；重复点击会唤回已有窗口。
若 `.bashrc` 的静态 SSH 别名中只有一个登录用户名，表单自动预填该用户，可自行修改。
已有版本保存的服务器继续保留：按别名或 IP 匹配 `.bashrc` 的直接 SSH 命令，
优先采用其中明确指定的用户和连接选项；不执行 `.bashrc` 或复杂 shell 命令。

填写的密码通过 libsecret 保存在系统钥匙环，配置文件、SSH 参数和环境变量中均不包含密码。
保存密码需要桌面 Secret Service（如 GNOME Keyring）；钥匙环不可用时会提示保存失败。
SSH 仍先尝试密钥，再通过 askpass 读取保存的密码；首次连接需要确认服务器主机密钥。
移除服务器也会清除对应钥匙环条目，不修改 `.bashrc` 或 `.ssh/config`。

从 SSH 工作台升级：应用现名 zTerm，启动命令为 `zterm`；旧命令 `ssh-workbench`
仍可使用。沿用原来的数据目录、配置目录和桌面入口标识，服务器列表和外观设置无需迁移。
deb 包名从 `ssh-workbench` 改为 `zterm`，安装新版时替换旧包。

## 构建 deb 安装包

在源码目录执行以下命令。打包使用 `dpkg-deb`，构建过程无需 root：

```sh
sudo apt install dpkg
# Python 语法编译检查；不会生成本机架构专用的可执行程序。
/usr/bin/python3 -m py_compile app.py model.py credentials.py quota.py
sh build_deb.sh
sudo apt install ./dist/zterm_0.2.5_all.deb
```

可通过 `sh build_deb.sh 0.2.6` 指定版本。安装包为 `Architecture: all`，
包含 Python 源码、桌面入口和图标，运行依赖由 apt 安装。
系统版启动命令为 `/usr/bin/zterm`，程序放在 `/usr/share/ssh-workbench/`，
文档放在 `/usr/share/doc/zterm/`，继续使用原有用户配置。

如果此前运行过 `install.sh`，用户级启动器和桌面入口仍优先于系统版；
可先通过 `/usr/bin/zterm` 明确启动 deb 版。安装包不会删除用户级安装。
卸载系统版使用 `sudo apt remove zterm`，用户配置保留。
用户级安装可删除对应的 `~/.local/bin/zterm` 和 `~/.local/bin/ssh-workbench`、上述应用数据目录，
以及用户数据目录下的 `applications/local.ssh.Workbench.desktop` 和
`icons/hicolor/scalable/apps/local.ssh.Workbench.svg`；保留配置目录即可保留设置。

## Codex 订阅额度

底栏对应当前选中机器上的 CLI 账号，后台每 5 分钟查询，也可手动刷新。
远端需要 Python 3.9+，可使用密钥、SSH agent 或已存入解锁钥匙环的密码，不会向终端注入命令。
先在终端会话中确认主机密钥；后台不会弹出主机确认、钥匙环解锁或密码输入窗口。
Codex 使用已登录 CLI 的官方 [account/rateLimits/read](https://learn.chatgpt.com/docs/app-server) 接口。
不读取或传输 Codex 登录凭据，不修改远端 CLI 配置。悬停可查看数据时间；
暂无数据或已过重置点时不会显示虚假的 0%。显示范围为 Codex 主/次额度，
模型专属额度及额外付费用量不在此范围。连接要求详见 [HELP.txt](HELP.txt)。

## 验证

测试需要 `xvfb` 和 `xauth`，使用临时配置与本地模拟进程，不连接真实 SSH 服务器。

```sh
sudo apt install xvfb xauth
/usr/bin/python3 -m py_compile app.py model.py credentials.py quota.py test_workbench.py test_quota.py test_credentials.py
/usr/bin/python3 test_credentials.py
/usr/bin/python3 test_quota.py
GDK_BACKEND=x11 xvfb-run -a /usr/bin/python3 test_workbench.py
```

测试涵盖独立服务器表单、右键编辑、侧栏排序持久化、旧配置兼容、密码保留/替换/清除及失败回滚、
异步保存期间的关闭/删除/重名处理、旧账号额度结果丢弃、askpass 目标匹配、SSH 用户/IP/端口覆盖、
配置保存、PTY 输入输出、会话切换和回收、外观保存恢复，
以及 14 套主题的实际 ANSI/前景/背景色块渲染、旧主题配置迁移、界面主题预览/保存/窗口隔离、标签布局/排序/溢出滚动、重排编号与 Alt 快捷键对应和统一底栏状态/重连。
“在线”依据当前会话进程状态；真实 SSH 断网场景尚未验证。
截图是测试生成物，不纳入版本控制。

详细用法和快捷键见 [HELP.txt](HELP.txt)，主题来源与适配约定见
[THEME_SOURCES.txt](THEME_SOURCES.txt)。终端配色只改变终端默认色及 ANSI 0–15，
不覆盖程序自行指定的 truecolor 颜色。

## 开源许可

Copyright (c) 2026 zhou411X。项目以 **GPL-3.0-only** 发布，完整条款见 [LICENSE](LICENSE)。
配色适配保留上游作者和许可声明，见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。
安装包包含程序源码和许可文本；每个 Release 的对应源码可通过同名 Git 标签获取。
