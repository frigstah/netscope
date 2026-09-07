"""NetScope core: interfaces, public IP, LAN sweep, port probing.

Pure standard library + a few Linux tools (ip, ping, avahi-resolve, curl).
No root required. Developed for and by frig.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

APP_NAME = "NetScope"
APP_ID = "io.github.frigstah.netscope"
TAGLINE = "developed for and by frig"
VERSION = "1.2.0"

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "netscope"
LAST_SCAN_FILE = CACHE_DIR / "last-scan.json"
PUBLIC_IP_FILE = CACHE_DIR / "public-ip.json"
OUI_FILE = Path("/usr/share/hwdata/oui.txt")

MAX_SWEEP_HOSTS = 1024
PING_WORKERS = 192
PROBE_CONCURRENCY = 384
PROBE_TIMEOUT = 0.9
BANNER_TIMEOUT = 0.8

ProgressCb = Optional[Callable[[int, int, str], None]]


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _json(cmd: list[str], timeout: float = 5.0):
    out = _run(cmd, timeout)
    try:
        return json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        return []


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #

@dataclass
class Address:
    addr: str
    prefix: int
    family: str  # "inet" | "inet6"
    scope: str = ""

    @property
    def cidr(self) -> str:
        return f"{self.addr}/{self.prefix}"


@dataclass
class Interface:
    name: str
    state: str
    mac: str = ""
    kind: str = ""
    mtu: int = 0
    addresses: list[Address] = field(default_factory=list)
    is_default: bool = False
    gateway: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0

    @property
    def ipv4(self) -> list[Address]:
        return [a for a in self.addresses if a.family == "inet"]

    @property
    def ipv6(self) -> list[Address]:
        return [a for a in self.addresses if a.family == "inet6" and a.scope != "link"]

    @property
    def up(self) -> bool:
        return self.state.upper() in ("UP", "UNKNOWN") and bool(self.ipv4 or self.ipv6)

    @property
    def networks(self) -> list[str]:
        nets = []
        for a in self.ipv4:
            try:
                nets.append(str(ipaddress.ip_network(a.cidr, strict=False)))
            except ValueError:
                pass
        return nets


def _iface_kind(name: str) -> str:
    if name == "lo":
        return "loopback"
    if name.startswith(("wl", "wlan", "wlp")):
        return "wifi"
    if name.startswith(("en", "eth")):
        return "ethernet"
    if name.startswith(("tun", "tap", "wg", "tailscale", "proton", "nordlynx")):
        return "vpn"
    if name.startswith(("docker", "br-", "virbr", "veth", "podman")):
        return "virtual"
    if name.startswith("usb"):
        return "usb"
    return "other"


def default_routes() -> dict[str, str]:
    """dev -> gateway for default routes."""
    out = {}
    for r in _json(["ip", "-j", "route", "show", "default"]):
        dev = r.get("dev")
        if dev:
            out[dev] = r.get("gateway", "")
    return out


def interfaces() -> list[Interface]:
    routes = default_routes()
    result: list[Interface] = []
    for i in _json(["ip", "-j", "-s", "addr"]):
        name = i.get("ifname", "?")
        addrs = []
        for a in i.get("addr_info", []):
            addrs.append(
                Address(
                    addr=a.get("local", ""),
                    prefix=int(a.get("prefixlen", 0)),
                    family=a.get("family", ""),
                    scope=str(a.get("scope", "")),
                )
            )
        stats = i.get("stats64", {}) or {}
        iface = Interface(
            name=name,
            state=i.get("operstate", "?"),
            mac=i.get("address", ""),
            kind=_iface_kind(name),
            mtu=int(i.get("mtu", 0)),
            addresses=addrs,
            is_default=name in routes,
            gateway=routes.get(name, ""),
            rx_bytes=int((stats.get("rx") or {}).get("bytes", 0)),
            tx_bytes=int((stats.get("tx") or {}).get("bytes", 0)),
        )
        result.append(iface)
    # default route first, then up, then the rest
    result.sort(key=lambda x: (not x.is_default, not x.up, x.kind == "loopback", x.name))
    return result


# --------------------------------------------------------------------------- #
# Public IP
# --------------------------------------------------------------------------- #

@dataclass
class PublicInfo:
    ipv4: str = ""
    ipv6: str = ""
    org: str = ""
    asn: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    hostname: str = ""
    fetched_at: float = 0.0
    error: str = ""


def _curl(url: str, v: str = "-4", timeout: float = 6.0) -> str:
    try:
        r = subprocess.run(
            ["curl", v, "-s", "--max-time", str(timeout), "-A", f"{APP_NAME}/{VERSION}", url],
            capture_output=True, text=True, timeout=timeout + 2, check=False,
        )
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def public_info(max_age: float = 600.0, force: bool = False) -> PublicInfo:
    """Public IPv4/IPv6 + ISP/geo details, cached for max_age seconds."""
    if not force:
        try:
            cached = json.loads(PUBLIC_IP_FILE.read_text())
            if time.time() - cached.get("fetched_at", 0) < max_age and cached.get("ipv4"):
                return PublicInfo(**cached)
        except (OSError, ValueError, TypeError):
            pass

    info = PublicInfo(fetched_at=time.time())
    v4 = ""
    for url in ("https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me/ip"):
        v4 = _curl(url, "-4")
        if v4 and _valid_ip(v4):
            break
        v4 = ""
    info.ipv4 = v4
    v6 = _curl("https://api6.ipify.org", "-6", 4.0)
    if v6 and _valid_ip(v6) and ":" in v6:
        info.ipv6 = v6

    if v4:
        raw = _curl("https://ipinfo.io/json", "-4")
        try:
            d = json.loads(raw) if raw else {}
        except ValueError:
            d = {}
        info.org = str(d.get("org", ""))
        if info.org.startswith("AS"):
            parts = info.org.split(" ", 1)
            info.asn = parts[0]
            info.org = parts[1] if len(parts) > 1 else ""
        info.city = str(d.get("city", ""))
        info.region = str(d.get("region", ""))
        info.country = str(d.get("country", ""))
        info.hostname = str(d.get("hostname", ""))
    else:
        info.error = "no route to the internet"

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        PUBLIC_IP_FILE.write_text(json.dumps(asdict(info)))
    except OSError:
        pass
    return info


def _valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# OUI vendor lookup
# --------------------------------------------------------------------------- #

_oui_cache: dict[str, str] = {}
_oui_loaded = False
_oui_lock = threading.Lock()


def _load_oui() -> None:
    global _oui_loaded
    with _oui_lock:
        if _oui_loaded:
            return
        _oui_loaded = True
        try:
            with OUI_FILE.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "(base 16)" in line:
                        prefix, _, vendor = line.partition("(base 16)")
                        _oui_cache[prefix.strip().upper()] = vendor.strip()
        except OSError:
            pass


def vendor_for_mac(mac: str) -> str:
    if not mac:
        return ""
    _load_oui()
    key = mac.replace(":", "").replace("-", "").upper()[:6]
    v = _oui_cache.get(key, "")
    if not v:
        # locally administered bit set -> randomized / virtual MAC
        try:
            if int(key[:2], 16) & 0x02:
                return "(private / randomized)"
        except ValueError:
            pass
    return v


# --------------------------------------------------------------------------- #
# LAN sweep
# --------------------------------------------------------------------------- #

@dataclass
class Host:
    ip: str
    mac: str = ""
    vendor: str = ""
    hostname: str = ""
    mdns: str = ""
    rtt_ms: float = -1.0
    seen_by: list[str] = field(default_factory=list)
    is_self: bool = False
    is_gateway: bool = False
    iface: str = ""

    @property
    def label(self) -> str:
        return self.hostname or self.mdns or ""

    @property
    def sort_key(self):
        try:
            return int(ipaddress.ip_address(self.ip))
        except ValueError:
            return 0


@dataclass
class ScanResult:
    network: str
    iface: str
    started_at: float
    duration: float
    hosts: list[Host]
    total_probed: int

    def to_json(self) -> dict:
        d = asdict(self)
        return d


def _ping(ip: str) -> Optional[float]:
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "1", "-n", "-q", ip],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("rtt") or "min/avg/max" in line:
            try:
                return float(line.split("=")[1].split("/")[1])
            except (IndexError, ValueError):
                return 0.0
    return 0.0


def neighbours(dev: str = "") -> dict[str, dict]:
    out = {}
    for n in _json(["ip", "-j", "neigh"]):
        if dev and n.get("dev") != dev:
            continue
        states = [s.upper() for s in n.get("state", [])]
        if any(s in ("FAILED", "INCOMPLETE") for s in states):
            continue
        ip = n.get("dst", "")
        if ":" in ip:
            continue
        out[ip] = {"mac": n.get("lladdr", ""), "state": ",".join(states), "dev": n.get("dev", "")}
    return out


def _rdns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def _mdns(ip: str) -> str:
    out = _run(["avahi-resolve", "-a", ip], timeout=1.5)
    parts = out.split()
    return parts[1] if len(parts) >= 2 else ""


def local_addresses() -> set[str]:
    return {a.addr for i in interfaces() for a in i.addresses}


def sweep(
    network: str,
    iface: str = "",
    progress: ProgressCb = None,
    resolve_names: bool = True,
    stop: Optional[threading.Event] = None,
    on_host: Optional[Callable[["Host"], None]] = None,
) -> ScanResult:
    """Ping-sweep a network, merge ARP table, resolve names and vendors."""
    net = ipaddress.ip_network(network, strict=False)
    if net.version != 4:
        raise ValueError("only IPv4 networks are swept")
    targets = [str(h) for h in net.hosts()]
    if len(targets) > MAX_SWEEP_HOSTS:
        targets = targets[:MAX_SWEEP_HOSTS]

    started = time.time()
    mine = local_addresses()
    gateways = set(default_routes().values())
    found: dict[str, Host] = {}
    done = 0
    total = len(targets)

    def report(msg: str):
        if progress:
            progress(done, total, msg)

    report("sweeping")
    with ThreadPoolExecutor(max_workers=PING_WORKERS) as pool:
        futs = {pool.submit(_ping, ip): ip for ip in targets}
        for fut in as_completed(futs):
            if stop and stop.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            ip = futs[fut]
            rtt = fut.result()
            done += 1
            if rtt is not None:
                h = found.setdefault(ip, Host(ip=ip, iface=iface))
                h.rtt_ms = rtt
                h.seen_by.append("icmp")
                report(f"echo from {ip}")
                if on_host:
                    on_host(h)
            elif done % 32 == 0:
                report("sweeping")

    # ARP / neighbour table: hosts that ignore ping still answer ARP
    for ip, n in neighbours(iface).items():
        if ipaddress.ip_address(ip) in net:
            h = found.setdefault(ip, Host(ip=ip, iface=iface))
            h.mac = n["mac"] or h.mac
            if "arp" not in h.seen_by:
                h.seen_by.append("arp")

    for h in found.values():
        h.is_self = h.ip in mine
        h.is_gateway = h.ip in gateways
        if h.is_self and not h.mac:
            for i in interfaces():
                if any(a.addr == h.ip for a in i.addresses):
                    h.mac = i.mac
        h.vendor = vendor_for_mac(h.mac)

    if resolve_names and found and not (stop and stop.is_set()):
        report("resolving names")
        with ThreadPoolExecutor(max_workers=32) as pool:
            jobs = {}
            for h in found.values():
                jobs[pool.submit(_rdns, h.ip)] = (h, "hostname")
                jobs[pool.submit(_mdns, h.ip)] = (h, "mdns")
            for fut in as_completed(jobs):
                h, attr = jobs[fut]
                try:
                    val = fut.result()
                except Exception:
                    val = ""
                if val:
                    setattr(h, attr, val)

    hosts = sorted(found.values(), key=lambda h: h.sort_key)
    result = ScanResult(
        network=str(net), iface=iface, started_at=started,
        duration=time.time() - started, hosts=hosts, total_probed=total,
    )
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SCAN_FILE.write_text(json.dumps(result.to_json()))
    except OSError:
        pass
    report("done")
    return result


def last_scan() -> Optional[dict]:
    try:
        return json.loads(LAST_SCAN_FILE.read_text())
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Port probe
# --------------------------------------------------------------------------- #

QUICK_PORTS = [
    20, 21, 22, 23, 25, 53, 67, 68, 69, 80, 81, 88, 110, 111, 119, 123, 135, 137, 138, 139,
    143, 161, 162, 179, 389, 427, 443, 445, 465, 500, 514, 515, 548, 554, 587, 631, 636,
    873, 902, 989, 990, 993, 995, 1080, 1194, 1433, 1434, 1521, 1723, 1883, 1900, 2049,
    2082, 2083, 2181, 2222, 2375, 2376, 3000, 3001, 3128, 3268, 3306, 3389, 3690, 4000,
    4200, 4243, 4443, 4567, 4848, 5000, 5001, 5060, 5061, 5353, 5432, 5555, 5601, 5672,
    5900, 5901, 5984, 5985, 5986, 6000, 6379, 6443, 6666, 6667, 7000, 7001, 7070, 7443,
    7547, 8000, 8001, 8008, 8009, 8010, 8020, 8025, 8080, 8081, 8083, 8086, 8088, 8089,
    8096, 8123, 8181, 8200, 8291, 8443, 8444, 8500, 8686, 8888, 8889, 8989, 9000, 9001,
    9090, 9091, 9092, 9100, 9117, 9200, 9300, 9418, 9443, 9696, 9999, 10000, 10250,
    11434, 27017, 32400, 49152, 49153, 51413, 62078,
]

SERVICE_HINTS = {
    81: "http-alt", 1883: "mqtt", 2375: "docker", 2376: "docker-tls", 3000: "http-dev",
    3128: "squid", 4243: "docker", 5000: "http-alt", 5601: "kibana", 5672: "amqp",
    5985: "winrm", 5986: "winrm-tls", 6379: "redis", 6443: "kube-api", 7547: "cwmp",
    8080: "http-proxy", 8081: "http-alt", 8086: "influxdb", 8096: "jellyfin",
    8123: "home-assistant", 8291: "mikrotik", 8443: "https-alt", 8883: "mqtt-tls",
    8888: "http-alt", 8989: "sonarr", 9000: "http-alt", 9090: "prometheus",
    9100: "jetdirect", 9117: "jackett", 9200: "elasticsearch", 9443: "https-alt",
    9696: "prowlarr", 10250: "kubelet", 11434: "ollama", 27017: "mongodb",
    32400: "plex", 51413: "transmission", 62078: "iphone-sync", 1900: "ssdp",
    5353: "mdns", 554: "rtsp", 7000: "airplay", 49152: "upnp", 49153: "upnp",
}

HTTP_PORTS = {80, 81, 3000, 5000, 8000, 8001, 8008, 8080, 8081, 8088, 8096, 8123, 8181,
              8888, 9000, 9090, 9091, 32400, 49152, 49153}

PROFILES = {
    "quick": ("Quick", "the ~150 ports that matter"),
    "common": ("Common", "1-1024 plus well-known high ports"),
    "full": ("Full", "all 65535 TCP ports"),
}


def profile_ports(name: str) -> list[int]:
    if name == "quick":
        return sorted(set(QUICK_PORTS))
    if name == "common":
        return sorted(set(range(1, 1025)) | set(QUICK_PORTS) | set(SERVICE_HINTS))
    if name == "full":
        return list(range(1, 65536))
    return parse_ports(name)


def parse_ports(spec: str) -> list[int]:
    """'22,80,8000-8100' -> sorted port list."""
    ports: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            lo, hi = max(1, min(lo, hi)), min(65535, max(lo, hi))
            ports.update(range(lo, hi + 1))
        else:
            try:
                p = int(part)
            except ValueError:
                continue
            if 1 <= p <= 65535:
                ports.add(p)
    return sorted(ports)


def service_name(port: int) -> str:
    if port in SERVICE_HINTS:
        return SERVICE_HINTS[port]
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


@dataclass
class PortHit:
    port: int
    service: str = ""
    banner: str = ""
    latency_ms: float = 0.0
    proto: str = "tcp"


async def _probe_one(ip: str, port: int, sem: asyncio.Semaphore, grab: bool) -> Optional[PortHit]:
    async with sem:
        t0 = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=PROBE_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            return None
        hit = PortHit(port=port, service=service_name(port), latency_ms=(time.perf_counter() - t0) * 1000)
        if grab:
            try:
                if port in HTTP_PORTS:
                    writer.write(b"HEAD / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n")
                    await writer.drain()
                data = await asyncio.wait_for(reader.read(256), timeout=BANNER_TIMEOUT)
                hit.banner = _clean_banner(data)
            except (OSError, asyncio.TimeoutError):
                pass
        try:
            writer.close()
        except OSError:
            pass
        return hit


def _clean_banner(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""
    # prefer a Server: header for HTTP, else first line
    for l in lines:
        if l.lower().startswith("server:"):
            return l[7:].strip()[:80]
    first = lines[0]
    return "".join(c if c.isprintable() else "." for c in first)[:80]


async def _probe_all(ip: str, ports: list[int], progress: ProgressCb, grab: bool,
                     stop: Optional[threading.Event],
                     on_hit: Optional[Callable[[PortHit], None]] = None) -> list[PortHit]:
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    hits: list[PortHit] = []
    done = 0
    total = len(ports)
    tasks = [asyncio.create_task(_probe_one(ip, p, sem, grab)) for p in ports]
    try:
        for coro in asyncio.as_completed(tasks):
            if stop and stop.is_set():
                break
            r = await coro
            done += 1
            if r:
                hits.append(r)
                if on_hit:
                    on_hit(r)
                if progress:
                    progress(done, total, f"open {r.port}/tcp {r.service}".rstrip())
            elif progress and done % 64 == 0:
                progress(done, total, "probing")
    finally:
        for t in tasks:
            t.cancel()
    return sorted(hits, key=lambda h: h.port)


def probe(ip: str, ports: Iterable[int], progress: ProgressCb = None,
          grab_banners: bool = True, stop: Optional[threading.Event] = None,
          on_hit: Optional[Callable[[PortHit], None]] = None) -> list[PortHit]:
    """TCP connect scan of `ports` on `ip`. Safe to call from a worker thread."""
    plist = list(ports)
    return asyncio.run(_probe_all(ip, plist, progress, grab_banners, stop, on_hit))


# --------------------------------------------------------------------------- #
# Aggregate status for the bar popout
# --------------------------------------------------------------------------- #

def status(refresh_public: bool = False) -> dict:
    ifs = []
    for i in interfaces():
        if i.kind == "loopback":
            continue
        ifs.append({
            "name": i.name, "state": i.state, "kind": i.kind, "mac": i.mac,
            "ipv4": [a.cidr for a in i.ipv4], "ipv6": [a.addr for a in i.ipv6],
            "default": i.is_default, "gateway": i.gateway, "up": i.up,
        })
    pub = public_info(force=refresh_public)
    scan = last_scan() or {}
    return {
        "app": APP_NAME, "version": VERSION, "tagline": TAGLINE,
        "interfaces": ifs,
        "public": asdict(pub),
        "lastScan": {
            "network": scan.get("network", ""),
            "iface": scan.get("iface", ""),
            "hosts": len(scan.get("hosts", [])),
            "at": scan.get("started_at", 0),
        },
    }
