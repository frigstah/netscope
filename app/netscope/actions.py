"""Per-device actions: open a web UI, SSH, ping, Wake-on-LAN. Developed for and
by frig. Best-effort launchers; nothing here needs root."""

from __future__ import annotations

import os
import shutil
import subprocess

from . import core

_TERMINALS = ["ghostty", "kitty", "alacritty", "foot", "wezterm", "xterm"]

# ports we would open in a browser, most-preferred first, with their scheme
_WEB_PREF = [(443, "https"), (8443, "https"), (9443, "https"), (80, "http"),
             (8080, "http"), (8000, "http"), (8123, "http"), (3000, "http"),
             (5000, "http"), (9000, "http"), (32400, "http"), (81, "http")]


def _terminal() -> list:
    """argv prefix that runs a command in a terminal. xdg-terminal-exec and
    uwsm-app take the command directly; classic terminals need -e."""
    t = os.environ.get("TERMINAL", "").split()
    if t and shutil.which(t[0]):
        base = os.path.basename(t[0])
        return t if base in ("xdg-terminal-exec", "uwsm-app") else t + ["-e"]
    if shutil.which("xdg-terminal-exec"):
        return ["xdg-terminal-exec"]
    for t in _TERMINALS:
        if shutil.which(t):
            return [t, "-e"]
    return []


def _detach(argv: list) -> bool:
    try:
        subprocess.Popen(argv, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def in_terminal(argv: list) -> bool:
    term = _terminal()
    if not term:
        return False
    return _detach(term + argv)


def open_url(url: str) -> bool:
    if not shutil.which("xdg-open"):
        return False
    return _detach(["xdg-open", url])


def _host_for_url(ip: str) -> str:
    """An IPv6 literal must be bracketed in a URL."""
    return f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip


def web_url(ip: str, open_ports: dict | None = None) -> str:
    """Pick the best URL for a device from its open ports (dict {port: svc} or
    a set of ints). Falls back to http://ip."""
    ports = set()
    if isinstance(open_ports, dict):
        ports = {int(p) for p in open_ports if str(p).isdigit()}
    elif open_ports:
        ports = {int(p) for p in open_ports}
    host = _host_for_url(ip)
    for port, scheme in _WEB_PREF:
        if port in ports:
            suffix = "" if port in (80, 443) else f":{port}"
            return f"{scheme}://{host}{suffix}"
    return f"http://{host}"


def open_web(ip: str, open_ports=None) -> bool:
    return open_url(web_url(ip, open_ports))


def ssh(ip: str, user: str = "") -> bool:
    target = f"{user}@{ip}" if user else ip
    return in_terminal(["ssh", target])


def ping(ip: str) -> bool:
    return in_terminal(["ping", ip])


def wake(mac: str) -> bool:
    return core.wake_on_lan(mac)
