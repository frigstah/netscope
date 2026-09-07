"""Desktop notifications via Omarchy (with a notify-send fallback).
Developed for and by frig."""

from __future__ import annotations

import shutil
import subprocess

from . import store

_GLYPH = {
    store.EV_NEW: "󰀩",     # new / unknown device
    store.EV_GONE: "󰀦",
    store.EV_BACK: "󰀨",
    store.EV_PORT_NEW: "󰊗",
}
_URGENCY = {
    store.EV_NEW: "critical",
    store.EV_PORT_NEW: "critical",
    store.EV_GONE: "low",
    store.EV_BACK: "low",
}


def _send(headline: str, body: str, glyph: str, urgency: str) -> None:
    if shutil.which("omarchy-notification-send"):
        cmd = ["omarchy-notification-send", "--app-name", "NetScope",
               "-u", urgency]
        if glyph:
            cmd += ["-g", glyph]
        cmd += [headline, body]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "-a", "NetScope", "-u", urgency, headline, body]
    else:
        return
    try:
        subprocess.run(cmd, timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _headline(ev: dict) -> tuple[str, str]:
    t = ev["type"]
    who = ev.get("name") or ev.get("ip") or ev.get("mac") or "a device"
    ip = ev.get("ip", "")
    if t == store.EV_NEW:
        return "New device on your network", f"{who}  ·  {ip}  ·  {ev.get('mac','')}"
    if t == store.EV_PORT_NEW:
        return "New open port", f"{ev.get('detail','')} on {who} ({ip})"
    if t == store.EV_GONE:
        return "Device left", f"{who}  ·  {ip}"
    if t == store.EV_BACK:
        return "Device back online", f"{who}  ·  {ip}"
    return "NetScope", who


# Which event types are worth interrupting the user for. Device arrivals and
# newly-opened ports are the security-relevant ones; comings and goings of
# already-known devices are logged but not shouted about.
DEFAULT_ALERTS = {store.EV_NEW, store.EV_PORT_NEW}


def notify_events(events: list[dict], types: set[str] | None = None) -> int:
    types = DEFAULT_ALERTS if types is None else types
    sent = 0
    for ev in events:
        if ev.get("type") not in types:
            continue
        # a randomized/private MAC (phones, some IoT) rejoins under a new MAC
        # every time, so it always looks "new" — never alert for those
        if ev.get("randomized") and ev.get("type") in (store.EV_NEW, store.EV_BACK, store.EV_GONE):
            continue
        head, body = _headline(ev)
        _send(head, body, _GLYPH.get(ev["type"], ""), _URGENCY.get(ev["type"], "normal"))
        sent += 1
    return sent
