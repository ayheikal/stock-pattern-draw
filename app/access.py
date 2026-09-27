"""Access key for devices other than this computer (phone mode).

Normally the app only listens on this computer. Started with `python -m app.serve --lan`, it also
answers on the home Wi-Fi so a phone can use it; then every device except this computer must show
the access key once (scanning the QR code on My profile does that), after which a cookie remembers it.
"""
from __future__ import annotations

import os
import secrets
import socket
from pathlib import Path
from urllib.parse import urlencode

COOKIE = "pc_key"
LOCAL = {"127.0.0.1", "::1", "localhost"}
# The install manifest and icons carry no data; the phone fetches them without the cookie.
PUBLIC = ("/manifest.webmanifest", "/static/icons/", "/static/style.css")
DEFAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "data" / "access_key.txt"


def lan_mode() -> bool:
    return os.environ.get("PATTERN_LAN") == "1"


def _key_file() -> Path:
    return Path(os.environ.get("PATTERN_KEY_FILE") or DEFAULT_KEY_FILE)


def get_key() -> str | None:
    """The access key in phone mode (created on first use), else None."""
    if not lan_mode():
        return None
    f = _key_file()
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(12)
    f.write_text(key, encoding="utf-8")
    return key


def allowed(client: str, path: str, cookie: str | None, key: str | None) -> bool:
    if key is None or client in LOCAL or path.startswith(PUBLIC):
        return True
    return bool(cookie) and secrets.compare_digest(cookie, key)


def lan_ip() -> str:
    """This computer's address on the home network (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def phone_link(port: int) -> dict:
    key = get_key()
    if key is None:
        return {"lan": False}
    base = f"http://{lan_ip()}:{port}"
    return {"lan": True, "url": f"{base}/profile?{urlencode({'key': key})}", "base": base}


LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pattern Charts</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main class="screener" style="max-width:420px;margin:40px auto">
<h1>🔒 Pattern Charts</h1>
<p class="muted">This app holds your portfolio, so other devices need the access key. On the computer running it,
open <b>My profile → Phone app</b> and scan the QR code, or type the key here.</p>
<form method="get" class="settings-bar" style="margin-top:12px"><label style="flex:1">Access key
<input name="key" autocomplete="off" style="width:100%"></label><button class="primary" type="submit">Open</button></form>
</main></body></html>"""
