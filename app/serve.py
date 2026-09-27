"""Run the app in the background (used by Windows auto-start).

    python -m app.serve            # this computer only, port 8000
    python -m app.serve --lan      # also reachable from your phone on the same Wi-Fi (access key required)

Logs go to data/server.log. If the port is already taken (e.g. a copy you started by hand), it exits.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class HideKey(logging.Filter):
    """Keep the phone access key out of the log (it arrives in the QR link's address)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "key=" in msg:
            record.msg, record.args = re.sub(r"key=[^&\s\"]+", "key=***", msg), ()
        return True


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Pattern Charts")
    ap.add_argument("--lan", action="store_true", help="also listen on the home network (phone app)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    os.chdir(ROOT)
    (ROOT / "data").mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(ROOT / "data" / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.addFilter(HideKey())
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("serve")

    if port_busy(args.port):
        log.warning("Port %s is already in use (another copy running?); not starting.", args.port)
        sys.exit(0)

    os.environ["PATTERN_PORT"] = str(args.port)
    if args.lan:
        os.environ["PATTERN_LAN"] = "1"
    import uvicorn

    log.info("Starting on port %s (%s)", args.port, "home network + this computer" if args.lan else "this computer only")
    uvicorn.run("app.main:app", host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, log_config=None)


if __name__ == "__main__":
    main()
