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


def _is_usable_ipv4(ip: str) -> bool:
    """A real address another device on the Wi-Fi can reach: IPv4,
    not loopback, not link-local (169.254.x.x)."""
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    if ip.startswith("127.") or ip.startswith("169.254."):
        return False
    return True


def _is_private_ipv4(ip: str) -> bool:
    """Home/office ranges first: 192.168.x, 10.x, 172.16-31.x."""
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            return 16 <= second <= 31
        except ValueError:
            return False
    return False


def _pick_best(candidates: list[str]) -> str | None:
    """Prefer a private-range address, then any usable IPv4."""
    usable = [ip for ip in candidates if _is_usable_ipv4(ip)]
    for ip in usable:
        if _is_private_ipv4(ip):
            return ip
    return usable[0] if usable else None


def _lan_ip_from_hostname_cmd() -> str | None:
    """`hostname -I` (Linux): all addresses of this machine, no
    network traffic at all. This is the one that works when the
    hostname itself resolves to 127.0.0.1 in /etc/hosts."""
    import shutil
    import subprocess

    if shutil.which("hostname") is None:
        return None
    try:
        out = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=5
        ).stdout.split()
        return _pick_best([ip for ip in out if ":" not in ip])
    except (OSError, subprocess.SubprocessError):
        return None


def _lan_ip_from_gateway() -> str | None:
    """Connect a UDP socket to the default gateway (read from
    /proc/net/route, a local address) and ask which local interface
    the OS would use. Connect on UDP sends nothing anywhere."""
    try:
        gateway = None
        with open("/proc/net/route", encoding="utf-8") as fh:
            next(fh)  # header
            for line in fh:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    gw_hex = fields[2]
                    gateway = ".".join(
                        str(int(gw_hex[i : i + 2], 16))
                        for i in (6, 4, 2, 0)
                    )
                    break
        if not gateway:
            return None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((gateway, 80))
            ip = sock.getsockname()[0]
        return ip if _is_usable_ipv4(ip) else None
    except OSError:
        return None


def get_lan_ip() -> str:
    """LAN IP (ex.: o IP da sua rede local) — the address other devices
    on the same Wi-Fi use to reach this machine. Resolved locally from
    this machine's own interfaces; sends nothing anywhere and never
    leaves the machine. Returns "localhost" only when no LAN address
    could be found at all."""
    for method in (
        _lan_ip_from_hostname_cmd,
        _lan_ip_from_gateway,
        lambda: _pick_best(
            [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)]
        ),
    ):
        try:
            ip = method()
        except OSError:
            ip = None
        if ip:
            return ip
    return "localhost"


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
        "localhost_url": f"http://localhost:{port}",
        "lan_ip": lan_ip,
        "lan_url": f"http://{lan_ip}:{port}",
    }
