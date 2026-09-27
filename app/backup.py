"""Daily copies of my profile, so a bad edit or a lost file isn't the end of my records.

Every save writes today's copy to data/backups (one file per day, the last KEEP days kept), and
optionally to a folder of my choice, e.g. a OneDrive / Google Drive folder, for an off-computer copy.
Copies include the Telegram token, like the profile itself; the download from the page leaves it out.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

KEEP = 30
PREFIX = "profile-"
_NAME = re.compile(r"^profile-(\d{4}-\d{2}-\d{2}|before-restore-\d{8}-\d{6})\.json$")
_status = {"last": None, "error": None}


def folder(profile_path: Path) -> Path:
    return profile_path.parent / "backups"


def _prune(d: Path) -> None:
    daily = sorted(p for p in d.glob(f"{PREFIX}????-??-??.json"))
    for old in daily[:-KEEP]:
        old.unlink(missing_ok=True)


def snapshot(profile_path: Path, extra_dir: str = "") -> None:
    """Copy the saved profile to today's backup (and the extra folder, when set)."""
    name = f"{PREFIX}{datetime.now():%Y-%m-%d}.json"
    d = folder(profile_path)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(profile_path, d / name)
    _prune(d)
    _status["last"], _status["error"] = datetime.now().isoformat(timespec="seconds"), None
    if extra_dir.strip():
        try:
            x = Path(extra_dir.strip()).expanduser()
            x.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(profile_path, x / name)
            _prune(x)
        except OSError as e:  # a missing cloud folder must never block saving
            _status["error"] = f"Couldn't copy to {extra_dir}: {e.strerror or e}"


def listing(profile_path: Path) -> dict:
    d = folder(profile_path)
    files = sorted((p for p in d.glob(f"{PREFIX}*.json") if _NAME.match(p.name)), reverse=True) if d.exists() else []
    return {"folder": str(d), "last": _status["last"], "error": _status["error"],
            "files": [{"name": p.name, "size": p.stat().st_size,
                       "saved": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")} for p in files]}


def restore(profile_path: Path, name: str, validate) -> None:
    """Put a backup back as the profile. The current profile is kept as a 'before-restore' copy first."""
    if not _NAME.match(name):
        raise ValueError("Unknown backup")
    src = folder(profile_path) / name
    if not src.exists():
        raise ValueError("Unknown backup")
    validate(json.loads(src.read_text(encoding="utf-8")))  # refuse a damaged file
    if profile_path.exists():
        shutil.copyfile(profile_path, folder(profile_path) / f"{PREFIX}before-restore-{datetime.now():%Y%m%d-%H%M%S}.json")
    tmp = profile_path.with_suffix(".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(profile_path)
