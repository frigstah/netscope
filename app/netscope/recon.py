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
        name = _unescape(parts[4] if len(parts) > 4 else "")
        typ = parts[5] if len(parts) > 5 else ""
        port = parts[8] if len(parts) > 8 else ""
        txt = _unescape(parts[9]) if len(parts) > 9 else ""
        label = name or typ
        key = (label, port)
        if key not in services:
            services[key] = MdnsService(service=label, port=port, txt=txt[:200])
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
    url = f"{scheme}://{ip}:{port}/"
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
    services = " ".join(getattr(s, "service", "") for s in (d.services or [])).lower()
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
    if ports & {9100, 515, 631} or "printer" in hay or "ipp" in services:
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
        lines.append(f"MAC vendor (OUI): {d.vendor}")
    if d.hostname:
        lines.append(f"Reverse-DNS hostname: {d.hostname}")
    if d.mdns and d.mdns != d.hostname:
        lines.append(f"mDNS hostname: {d.mdns}")
    if d.rtt_ms >= 0:
        lines.append(f"Ping latency: {d.rtt_ms:.1f} ms")
    if d.ttl:
        lines.append(f"Ping TTL: {d.ttl} -> {d.os_guess}")

    if d.services:
        lines.append("")
        lines.append("Advertised mDNS / Bonjour services:")
        for s in d.services:
            extra = f"  port {s.port}" if s.port and s.port != "0" else ""
            txt = f"  TXT[{s.txt}]" if s.txt else ""
            lines.append(f"  - {s.service}{extra}{txt}")

    if d.ports:
        lines.append("")
        lines.append("Open TCP ports (from a direct connect scan):")
        for h in d.ports:
            banner = f"  banner: {h.banner}" if h.banner else ""
            svc = h.service or "unknown"
            lines.append(f"  - {h.port}/tcp  {svc}{banner}")
    else:
        lines.append("")
        lines.append("Open TCP ports: none found in the scanned range (device may filter or expose none).")

    if d.https:
        lines.append("")
        lines.append("HTTP(S) responses:")
        for w in d.https:
            bits = [f"{w.scheme}://{d.ip}:{w.port}/"]
            if w.status:
                bits.append(f"status {w.status}")
            if w.server:
                bits.append(f"Server: {w.server}")
            if w.title:
                bits.append(f"title: {w.title}")
            if w.auth:
                bits.append(f"auth: {w.auth}")
            lines.append("  - " + "  |  ".join(bits))

    disc = getattr(d, "discovery", None)
    if disc is not None:
        up, nb, snmp = disc.upnp, disc.netbios, disc.snmp
        extra = []
        if up:
            bits = [x for x in (up.friendly_name, up.model, up.manufacturer) if x]
            if bits:
                extra.append("UPnP: " + " / ".join(bits))
            if up.device_type:
                extra.append("UPnP deviceType: " + up.device_type)
            if up.server:
                extra.append("UPnP server: " + up.server)
        if nb and (nb.name or nb.workgroup):
            extra.append(f"NetBIOS: {nb.name}" + (f" (workgroup {nb.workgroup})" if nb.workgroup else "")
                         + (" [server]" if nb.is_server else ""))
        if snmp:
            extra.append("SNMP sysDescr: " + snmp)
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
        loc = ", ".join(x for x in (pub.get("city"), pub.get("country")) if x)
        lines.append(f"Public: {pub['ipv4']} ({pub.get('org','')}{' · ' + loc if loc else ''})")
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
        name = g(d, "name") or g(d, "hostname") or ""
        vendor = g(d, "vendor", "")
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

