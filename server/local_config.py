"""Local-first helpers for FrogNews AutoCortes.

This module is the only place that knows about the machine the server
runs on: where finished cuts are saved, what the LAN IP is, and the
connection token the web app must present.

Nothing here touches the v3 render logic (server/pipeline.py) or the
viral scoring (server/viral.py).
"""
from __future__ import annotations

import json
import os
import secrets
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

VERSION = "1.1.0-local"


def jobs_dir() -> Path:
    """Job storage dir. Respects $JOBS_DIR so tests can point elsewhere."""
    return Path(os.environ.get("JOBS_DIR", BASE_DIR / "jobs")).resolve()


def config_path() -> Path:
    return BASE_DIR / "local_config.json"


def token_path() -> Path:
    return jobs_dir() / ".token"


def default_output_dir() -> Path:
    """Default folder for finished cuts. A real folder the user can
    find, not the internal jobs/ dir: ~/Videos/FrogNews-Cortes."""
    return Path.home() / "Videos" / "FrogNews-Cortes"


def load_config() -> dict:
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
    return {}


def save_config(data: dict) -> None:
    config_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_output_dir() -> Path:
    """Output folder, in priority order: $OUTPUT_DIR, local_config.json,
    then the default (~/Videos/FrogNews-Cortes). Always created."""
    env = os.environ.get("OUTPUT_DIR", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
    else:
        saved = load_config().get("output_dir", "")
        path = (
            Path(saved).expanduser().resolve()
            if saved
            else default_output_dir()
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_output_dir(path_str: str) -> Path:
    """Validate + create a new output folder and persist it.

    Raises ValueError on an empty path and OSError if the folder
    cannot be created (e.g. permission denied, path is a file).
    """
    raw = (path_str or "").strip()
    if not raw:
        raise ValueError("path is empty — send a folder path, e.g. C:\\Videos\\Cortes")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"{path} exists and is not a folder")
    path.mkdir(parents=True, exist_ok=True)
    config = load_config()
    config["output_dir"] = str(path)
    save_config(config)
    # Keep the process env in sync so a running server uses it at once.
    os.environ["OUTPUT_DIR"] = str(path)
    return path


def get_lan_ip() -> str:
    """LAN IP (e.g. 192.168.0.10) — the address other devices on the
    same Wi-Fi use to reach this machine. Uses a UDP connect, which
    opens no connection and sends no data."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_or_create_token() -> str:
    """Connection token. Created once, stored in jobs/.token (never
    committed — see .gitignore), reused on every later start."""
    path = token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows has no POSIX perms; the file still works
    return token


def token_exists() -> bool:
    path = token_path()
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


def check_token(provided: str | None) -> bool:
    """True when no token is configured (open) or the provided token
    matches the stored one."""
    if not token_exists():
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided, get_or_create_token())


def server_urls(port: int = 8000) -> dict[str, str]:
    lan_ip = get_lan_ip()
    return {
        "localhost_url": f"http://127.0.0.1:{port}",
        "lan_ip": lan_ip,
        "lan_url": f"http://{lan_ip}:{port}",
    }
