"""Persistent device inventory and event log. Developed for and by frig.

Foundation for continuous monitoring: remembers every device by MAC across
sweeps, tracks presence, and records what changed (new device, device gone,
new open port) as an append-only event log. Pure standard library.

Lives in ~/.local/state/netscope/ so it survives cache clears:
  inventory.json   device records keyed by a stable device key
  events.jsonl     append-only log of changes, newest appended last
  meta.json        schema version + whether a baseline has been seeded
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

SCHEMA = 1
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "netscope"
INVENTORY_FILE = STATE_DIR / "inventory.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"
META_FILE = STATE_DIR / "meta.json"

# a device must be missing from this many consecutive sweeps before it counts
# as "gone" — one dropped ping should not fire an alert
GONE_THRESHOLD = 2
EVENTS_KEEP = 2000

# event types
EV_NEW = "device_new"
EV_GONE = "device_gone"
EV_BACK = "device_back"
EV_PORT_NEW = "port_new"

ALERT_TYPES = {EV_NEW, EV_GONE, EV_BACK, EV_PORT_NEW}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _now() -> float:
    return time.time()


def is_randomized_mac(mac: str) -> bool:
    """Locally-administered (bit 0x02 of the first octet) => randomized/virtual;
    such a device may rejoin under a different MAC."""
    if not mac:
        return False
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def device_key(mac: str, ip: str) -> str:
    """Stable identity: normalized MAC when we have one, else the IP."""
    m = (mac or "").strip().lower()
    return m if m else f"ip:{ip}"


def _atomic_write(path: Path, text: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #

@dataclass
class Device:
    key: str
    mac: str = ""
    name: str = ""            # user label
    trusted: bool = False
    tags: list = field(default_factory=list)
    notes: str = ""
    vendor: str = ""
    hostname: str = ""        # best auto-discovered name (rdns/mdns)
    ip: str = ""              # most recent IP
    first_seen: float = 0.0
    last_seen: float = 0.0
    present: bool = True
    missed: int = 0
    randomized: bool = False
    is_self: bool = False
    is_gateway: bool = False
    ports: dict = field(default_factory=dict)   # {"22": "ssh", ...} last probe
    ports_at: float = 0.0
    risk: int = -1                              # last assessment score, -1 = never assessed

    @property
    def display_name(self) -> str:
        return self.name or self.hostname or self.vendor or self.ip

    @property
    def unknown(self) -> bool:
        return not self.trusted and not self.is_self


# --------------------------------------------------------------------------- #
# load / save
# --------------------------------------------------------------------------- #

def _load_raw() -> dict:
    try:
        return json.loads(INVENTORY_FILE.read_text())
    except (OSError, ValueError):
        return {}


def load() -> dict[str, Device]:
    out: dict[str, Device] = {}
    for key, d in _load_raw().items():
        try:
            fields = {k: d[k] for k in d if k in Device.__dataclass_fields__}
            fields["key"] = key
            out[key] = Device(**fields)
        except (TypeError, KeyError):
            continue
    return out


def save(devices: dict[str, Device]) -> None:
    data = {k: asdict(v) for k, v in devices.items()}
    _atomic_write(INVENTORY_FILE, json.dumps(data, indent=1))


def _meta() -> dict:
    try:
        return json.loads(META_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _set_meta(**kw) -> None:
    m = _meta()
    m.update(kw)
    m["schema"] = SCHEMA
    _atomic_write(META_FILE, json.dumps(m))


def seeded() -> bool:
    return bool(_meta().get("seeded"))


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #

def append_events(events: list[dict]) -> None:
    if not events:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
    except OSError:
        return
    _trim_events()


def _trim_events() -> None:
    try:
        lines = EVENTS_FILE.read_text().splitlines()
    except OSError:
        return
    if len(lines) > EVENTS_KEEP + 500:
        _atomic_write(EVENTS_FILE, "\n".join(lines[-EVENTS_KEEP:]) + "\n")


def recent_events(limit: int = 50) -> list[dict]:
    try:
        lines = EVENTS_FILE.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def _ev(kind: str, dev: Device, detail: str = "") -> dict:
    return {
        "ts": _now(), "type": kind, "key": dev.key, "ip": dev.ip,
        "mac": dev.mac, "name": dev.display_name, "detail": detail,
    }


# --------------------------------------------------------------------------- #
# recording
# --------------------------------------------------------------------------- #

def record_sweep(hosts: list) -> list[dict]:
    """Update the inventory from a sweep's hosts (core.Host). Returns the
    change events (empty on the very first sweep, which just seeds a baseline)."""
    devices = load()
    first_run = not seeded()
    now = _now()
    present: set[str] = set()
    events: list[dict] = []

    for h in hosts:
        key = device_key(h.mac, h.ip)
        present.add(key)
        d = devices.get(key)
        if d is None:
            d = Device(key=key, mac=h.mac, first_seen=now,
                       is_self=h.is_self, is_gateway=h.is_gateway,
                       trusted=h.is_self, randomized=is_randomized_mac(h.mac))
            devices[key] = d
            if not first_run:
                events.append(_ev(EV_NEW, _fill(d, h)))
        else:
            was_gone = not d.present
            _fill(d, h)
            if was_gone and not first_run:
                events.append(_ev(EV_BACK, d))
        _fill(d, h)
        d.last_seen = now
        d.present = True
        d.missed = 0

    for key, d in devices.items():
        if key in present or not d.present:
            continue
        d.missed += 1
        if d.missed >= GONE_THRESHOLD:
            d.present = False
            if not first_run:
                events.append(_ev(EV_GONE, d))

    save(devices)
    if first_run:
        _set_meta(seeded=True, first_sweep=now)
    append_events(events)
    return events


def _fill(d: Device, h) -> Device:
    d.mac = h.mac or d.mac
    d.ip = h.ip or d.ip
    d.vendor = h.vendor or d.vendor
    name = getattr(h, "hostname", "") or getattr(h, "mdns", "")
    if name:
        d.hostname = name
    d.is_self = d.is_self or h.is_self
    d.is_gateway = d.is_gateway or h.is_gateway
    if h.mac:
        d.randomized = is_randomized_mac(h.mac)
    return d


def record_probe(ip: str, mac: str, hits: list) -> list[dict]:
    """Store a probe's open ports for a device and emit port_new events for
    ports not seen on it before."""
    devices = load()
    key = device_key(mac, ip)
    d = devices.get(key)
    if d is None:
        return []
    new_ports = {str(h.port): h.service for h in hits}
    events = []
    if d.ports_at > 0:  # only alert once we have a prior baseline for this host
        for p, svc in new_ports.items():
            if p not in d.ports:
                events.append(_ev(EV_PORT_NEW, d, f"{p}/tcp {svc}".strip()))
    d.ports = new_ports
    d.ports_at = _now()
    save(devices)
    append_events(events)
    return events


# --------------------------------------------------------------------------- #
# edits + queries
# --------------------------------------------------------------------------- #

def update_device(key: str, **changes) -> Optional[Device]:
    devices = load()
    d = devices.get(key)
    if d is None:
        return None
    for k, v in changes.items():
        if k in Device.__dataclass_fields__ and k not in ("key",):
            setattr(d, k, v)
    save(devices)
    return d


def get(key: str) -> Optional[Device]:
    return load().get(key)


def summary() -> dict:
    devices = load()
    present = [d for d in devices.values() if d.present]
    unknown = [d for d in present if d.unknown]
    return {
        "total": len(devices),
        "present": len(present),
        "trusted": sum(1 for d in devices.values() if d.trusted),
        "unknown": len(unknown),
        "unknownPresent": [d.ip for d in unknown][:20],
        "recent": recent_events(8),
        "seeded": seeded(),
    }
