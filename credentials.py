#!/usr/bin/python3
"""OS keyring storage and OpenSSH askpass bridge; no plaintext secret files."""

import os
from pathlib import Path
import sys

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

SCHEMA = Secret.Schema.new("org.zterm.Server", Secret.SchemaFlags.NONE,
                           {"server": Secret.SchemaAttributeType.STRING})


def save_password(server_id, name, password):
    if not Secret.password_store_sync(SCHEMA, {"server": server_id}, Secret.COLLECTION_DEFAULT,
                                      "zTerm · " + name, password, None):
        raise RuntimeError("密码未保存到系统钥匙环。")


def delete_password(server_id):
    Secret.password_clear_sync(SCHEMA, {"server": server_id}, None)


def lookup_password(server_id, background=False):
    if not background:
        return Secret.password_lookup_sync(SCHEMA, {"server": server_id}, None)
    service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION, None)
    items = service.search_sync(SCHEMA, {"server": server_id}, Secret.SearchFlags.LOAD_SECRETS, None)
    for item in items:
        secret = item.get_secret()
        if secret is not None:
            return secret.get_text()
    return None


def ssh_environment(server_id, profile, background=False):
    if not profile or not profile.get("password_saved"):
        return {}
    return {"SSH_ASKPASS": str(Path(__file__).resolve()), "SSH_ASKPASS_REQUIRE": "force",
            "ZTERM_SERVER_ID": server_id, "ZTERM_SSH_USER": profile["username"],
            "ZTERM_SSH_HOST": profile["host"], "ZTERM_SSH_BACKGROUND": "1" if background else "0"}


def askpass(prompt):
    background = os.environ.get("ZTERM_SSH_BACKGROUND") == "1"
    expected = os.environ.get("ZTERM_SSH_USER", "") + "@" + os.environ.get("ZTERM_SSH_HOST", "")
    # Never supply the target server password for a host-key, key-passphrase,
    # or jump-host prompt. OpenSSH's ordinary password prompt identifies user/host.
    if prompt.strip() == expected + "'s password:":
        password = lookup_password(os.environ["ZTERM_SERVER_ID"], background)
        if password is not None:
            return password
    if background:
        return None
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    confirm = os.environ.get("SSH_ASKPASS_PROMPT") == "confirm"
    dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE,
                               text="zTerm · SSH 登录确认")
    dialog.format_secondary_text(prompt)
    dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "确认", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    entry = None
    if not confirm:
        entry = Gtk.Entry(visibility=False, activates_default=False)
        dialog.get_content_area().add(entry)
    dialog.show_all()
    accepted = dialog.run() == Gtk.ResponseType.OK
    answer = ("yes" if confirm else entry.get_text()) if accepted else None
    dialog.destroy()
    return answer


if __name__ == "__main__":
    try:
        answer = askpass(sys.argv[1] if len(sys.argv) > 1 else "")
    except Exception:
        # Diagnostics from keyring backends must not become SSH password input.
        sys.exit(1)
    if answer is None:
        sys.exit(1)
    sys.stdout.write(answer + "\n")
