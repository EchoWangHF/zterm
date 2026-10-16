"""Subscription quota bridge. Uses CLI login; never reads credential files.

This module is also sent over SSH to run with the remote user's Python 3.
Only normalized quota numbers leave the remote machine.
"""

import json
import math
import os
from pathlib import Path
import selectors
import shlex
import shutil
import subprocess
import sys
import time


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window(label, value, percent_key, reset_key):
    if not isinstance(value, dict):
        return None
    percent, reset = value.get(percent_key), value.get(reset_key)
    if not finite_number(percent) or not 0 <= percent <= 100:
        return None
    if reset is not None and (not finite_number(reset) or not 0 <= reset <= 253402300799):
        return None
    return {"label": label, "used": percent, "reset": reset}


def codex_windows(result):
    by_id = result.get("rateLimitsByLimitId") or {}
    limits = by_id.get("codex") or result.get("rateLimits") or {}
    windows = []
    for key, fallback in (("primary", "主额度"), ("secondary", "次额度")):
        value = limits.get(key)
        if not isinstance(value, dict):
            continue
        minutes = value.get("windowDurationMins")
        label = fallback
        if finite_number(minutes) and minutes > 0:
            label = f"{minutes / 1440:g}d" if minutes % 1440 == 0 else (
                f"{minutes / 60:g}h" if minutes % 60 == 0 else f"{minutes:g}m")
        item = window(label, value, "usedPercent", "resetsAt")
        if item:
            windows.append(item)
    return windows


def codex_quota(timeout=15):
    executable = shutil.which("codex")
    if not executable:
        for directory in (Path.home() / ".local/bin", Path.home() / "bin"):
            candidate = directory / "codex"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                executable = str(candidate)
                break
    if not executable:
        return {"message": "未安装 CLI"}
    try:
        process = subprocess.Popen([executable, "app-server"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return {"message": "CLI 无法启动"}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = b""
    deadline = time.monotonic() + timeout

    def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        process.stdin.flush()

    def receive(request_id):
        nonlocal pending
        while time.monotonic() < deadline:
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") == request_id:
                    return response
            if selector.select(max(0, deadline - time.monotonic())):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise OSError("CLI disconnected")
                pending += chunk
                if len(pending) > 1048576:
                    raise ValueError("Unexpected CLI response")
        raise TimeoutError("Quota request timed out")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ssh_workbench", "version": "0.2.0"}}})
        if "error" in receive(1):
            return {"message": "CLI 协议不兼容"}
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read"})
        response = receive(2)
        if "error" in response:
            return {"message": "无法获取：请检查 CLI 登录或网络"}
        windows = codex_windows(response["result"])
        return {"windows": windows, "observed": time.time(),
                "message": "" if windows else "账号未提供订阅额度"}
    except TimeoutError:
        return {"message": "查询超时"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"message": "CLI 额度读取失败"}
    finally:
        selector.close()
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def fetch(alias):
    """Run the same read-only probe locally or over noninteractive SSH."""
    source = Path(__file__).read_text()
    bootstrap = ("import json,sys; p=json.load(sys.stdin); "
                 "sys.argv=['quota.py']; "
                 "exec(compile(p['source'],'quota.py','exec'),"
                 "{'__name__':'__main__'})")
    command = [sys.executable, "-c", bootstrap]
    if alias:
        # Alias validation is shared with the terminal's OpenSSH entry point.
        from model import ssh_command
        command = ssh_command(alias)[:1] + ["-T", "-oBatchMode=yes", "-oConnectTimeout=5",
                                           alias, "python3 -c " + shlex.quote(bootstrap)]
    try:
        result = subprocess.run(command, input=json.dumps({"source": source}),
                                text=True, capture_output=True, timeout=25)
        if result.returncode:
            return {"error": "连接失败：需可用的免交互 SSH 登录和 Python 3" if alias else "额度查询程序失败"}
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not (
                "error" in value or isinstance(value.get("codex"), dict)):
            return {"error": "额度查询返回格式不正确"}
        return value
    except subprocess.TimeoutExpired:
        return {"error": "连接或额度查询超时"}
    except (OSError, ValueError):
        return {"error": "无法读取额度：请检查连接和 Python 3"}


def display(name, data, now=None):
    now = time.time() if now is None else now
    windows = data.get("windows", [])
    if not windows:
        return name + " · " + data.get("message", "暂无数据")
    parts = []
    for item in windows:
        reset = item["reset"]
        if reset is not None and reset <= now:
            parts.append(item["label"] + " 待刷新")
        else:
            reset_text = time.strftime("%m-%d %H:%M", time.localtime(reset)) if reset else "未知"
            parts.append(f'{item["label"]} 已用 {item["used"]:g}%（{reset_text} 重置）')
    return name + " · " + " · ".join(parts)


def main():
    try:
        value = {"codex": codex_quota()}
    except (OSError, ValueError, KeyError, TypeError):
        value = {"error": "额度查询失败；请检查 Codex CLI、登录状态和网络"}
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
