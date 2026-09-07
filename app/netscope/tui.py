"""Terminal UI (curses, no dependencies). Developed for and by frig.

A read-only-ish terminal view of the LAN: interfaces, Wi-Fi, public IP, and a
live host list you can sweep and probe. Built on core.py + store.py so it stays
in step with the GUI and CLI.

  s  sweep      p  probe selected     w  toggle watch
  ↑/↓ move      r  refresh public     q  quit
"""

from __future__ import annotations

import curses
import ipaddress
import threading
import time

from . import core, store, assess


class TuiState:
    def __init__(self):
        self.hosts: list = []
        self.sel = 0
        self.scanning = False
        self.probing = False
        self.status = "ready — press s to sweep"
        self.public = None
        self.wifi = None
        self.ports: list = []
        self.probe_ip = ""
        self.risk = None
        self.watch = False
        self.lock = threading.Lock()


def _default_net():
    for i in core.interfaces():
        if i.is_default and i.networks:
            return i.networks[0], i.name
    for i in core.interfaces():
        if i.up and i.kind != "loopback" and i.networks:
            return i.networks[0], i.name
    return "", ""


def _sweep(st: TuiState):
    net, iface = _default_net()
    if not net:
        st.status = "no network to sweep"
        return
    st.scanning = True
    st.status = f"sweeping {net}…"
    try:
        result = core.sweep(net, iface)
        store.record_sweep(result.hosts, result.network)
        devices = store.load()
        with st.lock:
            st.hosts = result.hosts
            st.sel = max(0, min(st.sel, len(st.hosts) - 1))
            for h in st.hosts:
                d = devices.get(store.device_key(h.mac, h.ip))
                h._trusted = bool(d and d.trusted)
                h._risk = d.risk if d and d.risk >= 0 else -1
                h._name = (d.name if d else "") or h.hostname or h.mdns
        st.status = f"{len(result.hosts)} hosts on {net} · {result.duration:.1f}s"
    except Exception as e:
        st.status = f"sweep failed: {e}"
    finally:
        st.scanning = False


def _probe(st: TuiState, ip: str, mac: str):
    st.probing = True
    st.probe_ip = ip
    st.status = f"probing {ip}…"
    try:
        hits = core.probe(ip, core.profile_ports("quick"))
        store.record_probe(ip, mac, hits)
        findings, score, lvl = assess.assess(hits)
        store.update_device(store.device_key(mac, ip), risk=score)
        with st.lock:
            st.ports = hits
            st.risk = (score, lvl)
        st.status = f"{ip}: {len(hits)} open · risk {score}/100 [{lvl}]"
    except Exception as e:
        st.status = f"probe failed: {e}"
    finally:
        st.probing = False


def _bg(fn, *a):
    threading.Thread(target=fn, *(), args=a, daemon=True).start()


def _draw(scr, st: TuiState):
    scr.erase()
    h, w = scr.getmaxyx()
    A = curses.A_BOLD
    try:
        scr.addstr(0, 0, "◢ NETSCOPE"[:w-1], curses.color_pair(1) | A)
        scr.addstr(0, 12, "// LAN reconnaissance (terminal)"[:max(0, w-13)], curses.color_pair(3))
        live = "BUSY" if (st.scanning or st.probing) else ("WATCH" if st.watch else "LIVE")
        scr.addstr(0, max(0, w - len(live) - 1), live, curses.color_pair(2) | A)
    except curses.error:
        pass

    # left column: interfaces / wifi / public
    y = 2
    def put(y, x, s, cp=0, bold=False):
        try:
            scr.addstr(y, x, s[:max(0, w - x - 1)], curses.color_pair(cp) | (A if bold else 0))
        except curses.error:
            pass

    put(y, 0, "INTERFACES", 1, True); y += 1
    for i in core.interfaces():
        if i.kind == "loopback":
            continue
        v4 = i.ipv4[0].cidr if i.ipv4 else "—"
        tag = "DEFAULT" if i.is_default else i.state.upper()
        put(y, 1, f"{i.name:<11} {tag:<7} {v4}", 0, i.up); y += 1
    if st.wifi:
        y += 1
        put(y, 0, "WI-FI", 1, True); y += 1
        put(y, 1, f"{st.wifi.get('ssid','')}  {st.wifi.get('signal_pct','?')}%  {st.wifi.get('band','')} ch{st.wifi.get('channel','')}  {st.wifi.get('security','')}"); y += 1
        put(y, 1, f"{st.wifi.get('rate','')}", 3); y += 1
    if st.public:
        y += 1
        put(y, 0, "PUBLIC", 1, True); y += 1
        p = st.public
        put(y, 1, p.get("ipv4") or p.get("error") or "—", 2, True); y += 1
        put(y, 1, f"{p.get('org','')}  {p.get('city','')} {p.get('country','')}", 3); y += 1

    # host list (right of a divider)
    lx = 40 if w > 90 else 0
    ly = 2 if lx else (y + 1)
    put(ly, lx, f"HOSTS ({len(st.hosts)})", 1, True); ly += 1
    put(ly, lx, f"{'IP':<16}{'':<5}{'NAME':<16}{'VEND':<16}{'RISK':<5}", 3); ly += 1
    with st.lock:
        hosts = list(st.hosts)
    view_h = h - ly - 6
    start = max(0, min(st.sel - view_h // 2, max(0, len(hosts) - view_h)))
    for idx in range(start, min(len(hosts), start + view_h)):
        hh = hosts[idx]
        mark = "SELF" if hh.is_self else ("GW" if hh.is_gateway else ("TR" if getattr(hh, "_trusted", False) else "UN"))
        risk = getattr(hh, "_risk", -1)
        rk = str(risk) if risk >= 0 else ""
        line = f"{hh.ip:<16}{mark:<5}{(getattr(hh,'_name','') or '')[:15]:<16}{(hh.vendor or '')[:15]:<16}{rk:<5}"
        cp = 2 if idx == st.sel else 0
        attr = curses.A_REVERSE if idx == st.sel else 0
        try:
            scr.addstr(ly, lx, line[:max(0, w - lx - 1)], curses.color_pair(cp) | attr)
        except curses.error:
            pass
        ly += 1

    # probe result panel (bottom)
    py = h - 5
    if st.ports or st.probe_ip:
        rk = f"  risk {st.risk[0]}/100 [{st.risk[1]}]" if st.risk else ""
        put(py, lx, f"PROBE {st.probe_ip}{rk}", 1, True); py += 1
        openp = " ".join(f"{x.port}/{x.service or 'tcp'}" for x in st.ports[:10])
        put(py, lx, openp or "no open ports", 3)

    put(h - 2, 0, st.status[:w-1], 3)
    put(h - 1, 0, "s sweep · p probe · w watch · ↑/↓ move · r public · q quit", 0)
    put(h - 1, max(0, w - 24), "developed for and by frig", 3)
    scr.refresh()


def _run(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_WHITE, -1)
    scr.nodelay(True)
    scr.timeout(200)

    st = TuiState()
    _bg(lambda: setattr(st, "public", __import__("dataclasses").asdict(core.public_info())))
    _bg(lambda: setattr(st, "wifi", core.wifi_status()))
    # seed from last scan
    scan = core.last_scan() or {}
    if scan.get("hosts"):
        st.hosts = [core.Host(**{k: hd.get(k) for k in ("ip", "mac", "vendor", "hostname", "mdns", "rtt_ms", "seen_by", "is_self", "is_gateway", "iface") if k in hd}) for hd in scan["hosts"]]
        devices = store.load()
        for hh in st.hosts:
            d = devices.get(store.device_key(hh.mac, hh.ip))
            hh._trusted = bool(d and d.trusted)
            hh._risk = d.risk if d and d.risk >= 0 else -1
            hh._name = (d.name if d else "") or hh.hostname
    _bg(_sweep, st)

    last_watch = 0
    while True:
        _draw(scr, st)
        if st.watch and not st.scanning and time.time() - last_watch > 120:
            last_watch = time.time()
            _bg(_sweep, st)
        try:
            c = scr.getch()
        except KeyboardInterrupt:
            break
        if c == -1:
            continue
        if c in (ord("q"), 27):
            break
        elif c in (curses.KEY_DOWN, ord("j")):
            st.sel = max(0, min(len(st.hosts) - 1, st.sel + 1))
        elif c in (curses.KEY_UP, ord("k")):
            st.sel = max(0, st.sel - 1)
        elif c == ord("s") and not st.scanning:
            _bg(_sweep, st)
        elif c == ord("p") and not st.probing:
            with st.lock:
                st.sel = max(0, min(st.sel, len(st.hosts) - 1))
                hh = st.hosts[st.sel] if st.hosts else None
            if hh is not None:
                _bg(_probe, st, hh.ip, hh.mac)
        elif c == ord("w"):
            st.watch = not st.watch
            st.status = "watch on" if st.watch else "watch off"
        elif c == ord("r"):
            _bg(lambda: setattr(st, "public", __import__("dataclasses").asdict(core.public_info(force=True))))


def main() -> int:
    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass
    return 0
