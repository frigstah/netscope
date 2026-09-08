"""Device fingerprinting: gather everything observable about a host, then
render it as a briefing for an AI investigation. Developed for and by frig.

Pure standard library + ip/avahi/curl. No root.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from . import core
from . import assess as _assess
from . import discover as _discover


def ev(value, cap: int = 160) -> str:
    """Every string below is chosen by the device being scanned and ends up in
    a terminal, a report and an AI prompt. Drop anything unprintable, which
    covers ANSI escapes and also the invisible characters a control-character
    class misses (U+202E right-to-left override, zero-width spaces), flatten
    newlines so a device cannot forge report sections or inject instructions,
    and cap the length so it cannot flood the prompt."""
    t = "".join(c if c.isprintable() else " " for c in str(value or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: cap - 1] + "\u2026") if len(t) > cap else t


# ev() bounds each field, but a single device can advertise thousands of mDNS
# services or hold thousands of ports open, and the briefing is billed per
# token, so the counts are bounded too.
_MAX_MDNS = 40
_MAX_PORTS = 64


# TTL -> OS family. Hops lower the observed value, so we snap up to the
# nearest common initial TTL.
def _os_from_ttl(ttl: int) -> str:
    if ttl <= 0:
        return ""
    if ttl <= 64:
        return "Linux / Unix / Android / macOS (initial TTL 64)"
    if ttl <= 128:
        return "Windows (initial TTL 128)"
    return "network gear / other (initial TTL 255)"


def _ping_ttl(ip: str) -> int:
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "1", "-n", ip],
                             capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = re.search(r"ttl=(\d+)", out, re.IGNORECASE)
    return int(m.group(1)) if m else 0


@dataclass
class MdnsService:
    service: str
    port: str = ""
    txt: str = ""
    instance: str = ""
    stype: str = ""


def _mdns_services(ip: str, timeout: float = 6.0) -> list[MdnsService]:
    """Resolve every advertised mDNS/DNS-SD service for one host."""
    try:
        out = subprocess.run(["avahi-browse", "-a", "-t", "-r", "-p", "-k"],
                             capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    services: dict[tuple, MdnsService] = {}
    for line in out.splitlines():
        if not line.startswith("="):
            continue
        # =;iface;proto;name;type;domain;host;address;port;txt
        parts = line.split(";")
        if len(parts) < 9:
            continue
        if parts[7] != ip:
            continue
        instance = ev(_unescape(parts[3] if len(parts) > 3 else ""), 48)
        typ = ev(parts[4] if len(parts) > 4 else "", 48)
        port = ev(parts[8] if len(parts) > 8 else "", 8)
        txt = ev(_unescape(parts[9]) if len(parts) > 9 else "", 160)
        # the instance name ("Living Room Camera") is the most identifying
        # string a device advertises, so it travels next to the service type
        # instead of being dropped, and two instances of one type no longer
        # collapse into each other
        label = f"{instance} ({typ})" if instance and typ else (instance or typ)
        key = (instance, typ, port)
        if key not in services:
            services[key] = MdnsService(service=label, port=port, txt=txt,
                                        instance=instance, stype=typ)
            if len(services) >= _MAX_MDNS:
                break
    return list(services.values())


def _unescape(s: str) -> str:
    return s.replace("\\032", " ").replace("\\.", ".").replace("\\059", ";").strip()


@dataclass
class HttpInfo:
    port: int
    scheme: str
    status: str = ""
    server: str = ""
    title: str = ""
    location: str = ""
    auth: str = ""


def _http_probe(ip: str, port: int, scheme: str, timeout: float = 4.0) -> Optional[HttpInfo]:
    host = f"[{ip}]" if ":" in ip else ip
    url = f"{scheme}://{host}:{port}/"
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-k", "-L", "--max-redirs", "2",
             "-A", "Mozilla/5.0 (NetScope)", "-D", "-", url],
            capture_output=True, text=True, timeout=timeout + 2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 and not r.stdout:
        return None
    text = r.stdout
    head, _, body = text.partition("\r\n\r\n")
    if not body:
        head, _, body = text.partition("\n\n")
    info = HttpInfo(port=port, scheme=scheme)
    status = re.search(r"HTTP/[\d.]+\s+(\d{3}[^\r\n]*)", head)
    if status:
        info.status = status.group(1).strip()
    for hdr, attr in (("server", "server"), ("location", "location"), ("www-authenticate", "auth")):
        m = re.search(rf"^{hdr}:\s*(.+)$", head, re.IGNORECASE | re.MULTILINE)
        if m:
            setattr(info, attr, m.group(1).strip()[:120])
    t = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
    if t:
        info.title = re.sub(r"\s+", " ", t.group(1)).strip()[:120]
    if not (info.status or info.server or info.title):
        return None
    return info


@dataclass
class Dossier:
    ip: str
    mac: str = ""
    vendor: str = ""
    hostname: str = ""
    mdns: str = ""
    is_gateway: bool = False
    is_self: bool = False
    rtt_ms: float = -1.0
    ttl: int = 0
    os_guess: str = ""
    ports: list = field(default_factory=list)       # core.PortHit
    services: list = field(default_factory=list)     # MdnsService
    https: list = field(default_factory=list)        # HttpInfo
    discovery: object = None                          # discover.Discovery
    dtype: str = ""                                   # best device-type guess


def fingerprint(ip: str, host: Optional["core.Host"], hits: list,
                progress=None) -> Dossier:
    """Collect every observable signal about `ip`. `hits` are prior probe results."""
    def say(msg):
        if progress:
            progress(msg)

    d = Dossier(ip=ip)
    if host is not None:
        d.mac = host.mac
        d.vendor = host.vendor
        d.hostname = host.hostname
        d.mdns = host.mdns
        d.is_gateway = host.is_gateway
        d.is_self = host.is_self
        d.rtt_ms = host.rtt_ms
    if not d.vendor and d.mac:
        d.vendor = core.vendor_for_mac(d.mac)
    if not d.hostname:
        d.hostname = core._rdns(ip)

    say("reading TTL")
    d.ttl = _ping_ttl(ip)
    d.os_guess = _os_from_ttl(d.ttl)

    say("resolving mDNS services")
    d.services = _mdns_services(ip)

    say("active discovery (upnp/netbios/snmp)")
    try:
        d.discovery = _discover.enrich(ip, progress=lambda m: say(m))
    except Exception:
        d.discovery = None

    d.ports = list(hits)
    for h in d.ports:
        if h.port in core.HTTP_PORTS or h.service in ("http", "https", "http-alt", "https-alt", "http-proxy"):
            scheme = "https" if (h.port in (443, 8443, 4443, 9443, 8444, 7443) or "https" in h.service) else "http"
            say(f"http probe {h.port}")
            info = _http_probe(ip, h.port, scheme)
            if info:
                d.https.append(info)
    d.dtype = guess_type(d)
    return d


_CAMERA_VENDORS = ("d-link", "hikvision", "dahua", "reolink", "amcrest", "wyze", "axis", "ubiquiti")
_ROUTER_VENDORS = ("asustek", "asus", "tp-link", "netgear", "ubiquiti", "mikrotik", "d-link", "zyxel")


def guess_type(d: "Dossier") -> str:
    """Best-effort device category from every signal we have."""
    ports = {h.port for h in (d.ports or [])}
    vendor = (d.vendor or "").lower()
    up = getattr(d, "discovery", None)
    up = up.upnp if up else None
    utype = (up.device_type or "").lower() if up else ""
    umodel = (up.model or "").lower() if up else ""
    # the DNS-SD type only, never the device-chosen instance name: the rules
    # below match short substrings, so "Philipp's MacBook" would read as a
    # printer ("ipp") and "Podcast Archive" as a media box ("cast")
    services = " ".join(getattr(s, "stype", "") for s in (d.services or [])).lower()
    hay = f"{vendor} {umodel} {services} {d.hostname or ''}".lower()

    file_ports = ports & {445, 139, 2049, 548}
    is_camera_vendor = any(v in vendor for v in _CAMERA_VENDORS) and ("cam" in hay or "dcs" in hay or "ipc" in hay)

    if d.is_gateway or "internetgateway" in utype:
        return "router / gateway"
    if "synology" in vendor or "qnap" in vendor or "truenas" in hay or (ports & {5000, 5001} and file_ports):
        return "NAS / file server"
    if "mediarenderer" in utype or "mediaserver" in utype or ports & {8009, 32400, 8008} or "airplay" in services or "cast" in services or "shield" in services:
        return "media / streaming device"
    if is_camera_vendor or ((ports & {554} or "rtsp" in services) and not file_ports):
        return "camera"
    # 9100/515 are print-only; 631 (CUPS) also runs on ordinary desktops, so it
    # only counts with corroboration
    if (ports & {9100, 515} or "printer" in hay or "ipp" in services
            or (631 in ports and ("print" in hay or not (ports - {631, 22, 5353})))):
        return "printer"
    if "proxmox" in vendor:
        return "VM / container"
    if "home-assistant" in services or 8123 in ports:
        return "home automation hub"
    if any(v in vendor for v in ("dreame", "roborock", "ecovacs")):
        return "robot vacuum"
    if any(v in vendor for v in ("espressif", "tuya", "sonoff", "shelly", "winner micro")) or ports & {1883, 8883}:
        return "IoT device"
    if file_ports:
        return "file server / Windows host"
    if any(v in vendor for v in _ROUTER_VENDORS):
        return "network gear (AP/switch/router)"
    if "apple" in vendor:
        return "Apple device"
    if "google" in vendor:
        return "Google device"
    if 22 in ports and not (ports - {22}):
        return "server / headless host"
    return "unknown"


def build_prompt(d: Dossier) -> str:
    """Turn a dossier into a briefing for the AI investigator."""
    lines = []
    lines.append(f"Target: {d.ip}")
    if d.is_gateway:
        lines.append("Role hint: this is the default gateway / router for the LAN.")
    if d.is_self:
        lines.append("Role hint: this is the machine running the scan.")
    if d.mac:
        lines.append(f"MAC: {d.mac}")
    if d.vendor:
        lines.append(f"MAC vendor (OUI): {ev(d.vendor, 80)}")
    if d.hostname:
        lines.append(f"Reverse-DNS hostname: {ev(d.hostname, 80)}")
    if d.mdns and d.mdns != d.hostname:
        lines.append(f"mDNS hostname: {ev(d.mdns, 80)}")
    if d.rtt_ms >= 0:
        lines.append(f"Ping latency: {d.rtt_ms:.1f} ms")
    if d.ttl:
        lines.append(f"Ping TTL: {d.ttl} -> {d.os_guess}")

    if d.services:
        lines.append("")
        lines.append("Advertised mDNS / Bonjour services:")
        for s in d.services[:_MAX_MDNS]:
            extra = f"  port {s.port}" if s.port and s.port != "0" else ""
            txt = f"  TXT[{ev(s.txt, 120)}]" if s.txt else ""
            lines.append(f"  - {ev(s.service, 100)}{extra}{txt}")
        if len(d.services) > _MAX_MDNS:
            lines.append(f"  - ... {len(d.services) - _MAX_MDNS} further services advertised, not listed")

    if d.ports:
        lines.append("")
        lines.append("Open TCP ports (from a direct connect scan):")
        for h in d.ports[:_MAX_PORTS]:
            banner = f"  banner: {ev(h.banner, 120)}" if h.banner else ""
            svc = ev(h.service, 40) or "unknown"
            lines.append(f"  - {h.port}/tcp  {svc}{banner}")
        if len(d.ports) > _MAX_PORTS:
            lines.append(f"  - ... {len(d.ports) - _MAX_PORTS} further open ports, not listed")
    else:
        lines.append("")
        lines.append("Open TCP ports: none found in the scanned range (device may filter or expose none).")

    if d.https:
        lines.append("")
        lines.append("HTTP(S) responses:")
        for w in d.https:
            bits = [f"{w.scheme}://{d.ip}:{w.port}/"]
            if w.status:
                bits.append(f"status {ev(w.status, 40)}")
            if w.server:
                bits.append(f"Server: {ev(w.server, 80)}")
            if w.title:
                bits.append(f"title: {ev(w.title, 100)}")
            if w.auth:
                bits.append(f"auth: {ev(w.auth, 60)}")
            lines.append("  - " + "  |  ".join(bits))

    disc = getattr(d, "discovery", None)
    if disc is not None:
        up, nb, snmp = disc.upnp, disc.netbios, disc.snmp
        extra = []
        if up:
            bits = [x for x in (up.friendly_name, up.model, up.manufacturer) if x]
            if bits:
                extra.append("UPnP: " + ev(" / ".join(bits), 160))
            if up.device_type:
                extra.append("UPnP deviceType: " + ev(up.device_type, 100))
            if up.server:
                extra.append("UPnP server: " + ev(up.server, 100))
        if nb and (nb.name or nb.workgroup):
            extra.append(f"NetBIOS: {ev(nb.name, 40)}" + (f" (workgroup {ev(nb.workgroup, 40)})" if nb.workgroup else "")
                         + (" [server]" if nb.is_server else ""))
        if snmp:
            extra.append("SNMP sysDescr: " + ev(snmp, 200))
        if extra:
            lines.append("")
            lines.append("Active discovery:")
            for e in extra:
                lines.append("  - " + e)
    if getattr(d, "dtype", ""):
        lines.append("")
        lines.append(f"Heuristic device type (local guess): {d.dtype}")

    # local rules-based security assessment, so the model corroborates rather
    # than invents
    try:
        findings, score, level = _assess.assess(d.ports, d)
        real = [f for f in findings if f.severity != "info"]
        if real:
            lines.append("")
            lines.append(f"Local security assessment (rules-based): risk {score}/100 [{level}]")
            for f in real:
                where = f" ({f.port}/tcp)" if f.port else ""
                lines.append(f"  - [{f.severity}] {f.title}{where}: {f.detail}")
    except Exception:
        pass

    return "\n".join(lines)


SYSTEM_BRIEF = (
    "You are a network reconnaissance analyst. You are given passively- and "
    "actively-gathered evidence about a single device on a local network. "
    "Identify the device as precisely as the evidence allows and explain it "
    "plainly. Do not invent facts the evidence does not support; when you infer, "
    "say what the inference rests on and how confident you are. Never suggest "
    "attacking, exploiting, or gaining unauthorized access. This is the analyst's "
    "own network; the goal is inventory and security awareness.\n\n"
    "Write a tight report in plain text (no markdown symbols like # or *), using "
    "these upper-case section headers, each followed by short lines:\n\n"
    "IDENTITY\n"
    "  Best guess at manufacturer, model/product, and device type (router, NAS, "
    "phone, TV, camera, server, IoT sensor, printer, etc.). Give a confidence word "
    "(certain / likely / possible) and the evidence behind it.\n\n"
    "WHAT IT IS AND DOES\n"
    "  2-5 lines on the device's purpose and main functions.\n\n"
    "OPEN PORTS\n"
    "  One line per open port: 'PORT/tcp  service  -  what this service is, and "
    "what it is most likely being used for on THIS device'. Tie it to the identity "
    "above where you can.\n\n"
    "SECURITY NOTES\n"
    "  Brief, factual observations an owner should know (exposed admin panels, "
    "plaintext protocols, default-looking services, notable open ports). No exploit "
    "instructions - awareness only. If nothing stands out, say so.\n\n"
    "Keep the whole thing under ~350 words."
)


def build_port_prompt(d: Dossier, port: int) -> str:
    """Same device evidence as build_prompt, but focused on one open port the
    owner clicked, so the model answers 'how do I reach and log into this
    service on this device'."""
    focus = []
    hit = next((h for h in (d.ports or []) if int(getattr(h, "port", 0)) == int(port)), None)
    svc = ev(getattr(hit, "service", ""), 40) if hit else ""
    banner = ev(getattr(hit, "banner", ""), 120) if hit else ""
    focus.append(f"FOCUS PORT: {port}/tcp on {d.ip}"
                 + (f" - service '{svc}'" if svc else "")
                 + (f" - banner '{banner}'" if banner else ""))
    # any HTTP(S) response already seen on this exact port is the strongest hint
    for w in (d.https or []):
        if str(getattr(w, "port", "")) == str(port):
            bits = [f"{w.scheme}://{d.ip}:{w.port}/"]
            if w.status:
                bits.append(f"status {ev(w.status, 40)}")
            if w.title:
                bits.append(f"title '{ev(w.title, 100)}'")
            if w.auth:
                bits.append(f"auth '{ev(w.auth, 60)}'")
            focus.append("HTTP seen on this port: " + "  |  ".join(bits))
    focus.append("")
    return "\n".join(focus) + build_prompt(d)


PORT_ACCESS_BRIEF = (
    "You are a network administrator's assistant. The analyst owns this local "
    "network and its devices, and wants to log into or connect to a service on "
    "ONE of their own devices. You are given evidence about the device and one "
    "focus port. Explain how the owner reaches that service on that device: the "
    "likely address, the client to use, and the factory-default credentials the "
    "vendor ships, which is public information from the product's own "
    "documentation. This is legitimate first-party access to the owner's "
    "equipment, not an attack.\n\n"
    "Do not invent product specifics the evidence does not support; when you "
    "infer from the vendor or banner, say so and give a confidence word "
    "(certain / likely / possible). Stay within accessing the owner's own "
    "device: do NOT give techniques for defeating authentication - no brute "
    "forcing, password spraying, credential stuffing, default-credential "
    "scanning of other people's systems, or exploiting a vulnerability to get "
    "in. Factory defaults and the normal login URL are the scope.\n\n"
    "Write a tight plain-text answer (no markdown symbols like # or *), using "
    "these upper-case headers:\n\n"
    "SERVICE\n"
    "  One line: what runs on this port on THIS device, tied to its identity.\n\n"
    "HOW TO REACH IT\n"
    "  The most likely address(es), most-likely first. For a web UI give the "
    "full URL(s) including scheme and port and any common path (for example a "
    "Synology NAS admin UI, a camera's web page, a router's console). For SSH "
    "give a ready ssh command. For other services (RTSP, SMB, database, VNC, "
    "etc.) give the client and a ready-to-paste connection command or string. "
    "Note if it is likely HTTPS with a self-signed certificate.\n\n"
    "CLIENT / TOOL\n"
    "  What to open it with (a browser, ssh, an app, a specific client) and any "
    "flag that commonly matters.\n\n"
    "DEFAULT CREDENTIALS\n"
    "  The vendor's documented factory-default username/password for this device "
    "or service, with a confidence word. If the defaults are blank, first-boot "
    "setup, or account-creation-on-first-use, say that. If you do not know the "
    "defaults for this exact product, say so rather than guessing. Always add "
    "one line telling the owner to change any still-default credentials.\n\n"
    "NOTES\n"
    "  Anything else that helps the owner get in on the first try (a setup "
    "wizard, a companion mobile app, a non-obvious port or path). Keep the whole "
    "answer under ~300 words."
)


NETWORK_BRIEF = (
    "You are a home/small-office network analyst. You are given an inventory of "
    "every device seen on the local network, with names, vendors, open ports "
    "(where known) and a local rules-based risk score. Summarize the network "
    "plainly for its owner. Do not invent devices or facts beyond the evidence; "
    "when you infer, say so. Never suggest attacking or exploiting anything; the "
    "goal is inventory and security awareness on the owner's own network.\n\n"
    "Write a tight plain-text report (no markdown symbols like # or *), using "
    "these upper-case headers:\n\n"
    "OVERVIEW\n"
    "  How many devices, what kinds dominate (servers, IoT, phones, media, "
    "printers), and anything notable about the shape of the network.\n\n"
    "NOTABLE DEVICES\n"
    "  A short list of the most interesting or highest-risk devices, one line "
    "each: name/ip - what it is - why it stands out.\n\n"
    "RISKS\n"
    "  The most important exposure themes across the network (plaintext admin, "
    "exposed databases, unknown/untrusted devices, IoT), most important first. "
    "Awareness only, no exploit steps.\n\n"
    "RECOMMENDATIONS\n"
    "  A few concrete, safe things the owner could do (segment IoT, disable a "
    "plaintext service, name/verify unknown devices).\n\n"
    "Keep it under ~400 words."
)


def build_network_prompt(devices: list, public: dict) -> str:
    """Compact inventory listing from stored devices (store.Device-like dicts
    or objects) plus public info. No re-probing; uses what is already known."""
    def g(d, k, default=""):
        return d.get(k, default) if isinstance(d, dict) else getattr(d, k, default)

    lines = []
    pub = public or {}
    if pub.get("ipv4"):
        # the address, ISP and city identify the owner, not the network's
        # security posture, and this prompt is what leaves for a cloud engine
        lines.append("Public connectivity: present (address withheld).")
    present = [d for d in devices if g(d, "present", True)]
    lines.append(f"{len(present)} devices present ({len(devices)} known total).")
    lines.append("")
    lines.append("Devices (flags: T=trusted S=self G=gateway U=unknown R=randomized-MAC):")

    def sort_key(d):
        risk = g(d, "risk", -1)
        return (-(risk if isinstance(risk, (int, float)) else -1), str(g(d, "ip", "")))

    for d in sorted(present, key=sort_key):
        flags = "".join([
            "S" if g(d, "is_self", False) else ("G" if g(d, "is_gateway", False)
                 else ("T" if g(d, "trusted", False) else "U")),
            "R" if g(d, "randomized", False) else "",
        ])
        name = ev(g(d, "name") or g(d, "hostname") or "", 48)
        vendor = ev(g(d, "vendor", ""), 48)
        ip = g(d, "ip", "")
        risk = g(d, "risk", -1)
        ports = g(d, "ports", {}) or {}
        pstr = ",".join(sorted(ports.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)[:16]) if ports else ""
        bits = [f"[{flags:<2}] {ip:<15}"]
        if name:
            bits.append(name)
        if vendor and vendor != name:
            bits.append(f"({vendor})")
        if isinstance(risk, (int, float)) and risk >= 0:
            bits.append(f"risk {int(risk)}")
        if pstr:
            bits.append(f"ports {pstr}")
        lines.append("  - " + "  ".join(bits))

    return "\n".join(lines)

