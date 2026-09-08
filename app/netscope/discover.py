"""Active service discovery to sharpen device identification. Developed for and
by frig.

All unprivileged UDP: SSDP/UPnP (1900), NetBIOS name service (137), and SNMP
sysDescr with the public community (161). Everything is best-effort and short-
timeout; nothing here needs root.
"""

from __future__ import annotations

import re
import socket
import time
from urllib.parse import urlparse
import struct
import subprocess
from dataclasses import dataclass, field
from typing import Optional


def _safe(value, cap: int) -> str:
    """Everything this module extracts is chosen by the device being probed and
    ends up in a terminal, a report and an AI prompt. Blank out every
    non-printable character (the C0/C1 controls that drive a terminal, but also
    the zero-width and bidi-override formatters that disguise what it shows),
    flatten newlines (so a device cannot forge report sections or inject
    instructions) and cap the length (so it cannot flood the prompt)."""
    t = "".join(c if c.isprintable() else " " for c in str(value or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return (t[: cap - 1] + "…") if len(t) > cap else t


# --------------------------------------------------------------------------- #
# SSDP / UPnP
# --------------------------------------------------------------------------- #

_SSDP_MCAST = ("239.255.255.250", 1900)

# The responder decides how often it answers, so a per-recv timeout alone lets
# one chatty device hold the worker thread forever. Bound the loops by wall
# clock and by reply count as well.
MAX_SSDP_REPLIES = 64
MAX_SSDP_TYPES = 16
MAX_HEADER_CHARS = 120
# LOCATION is a URL we hand to curl, so it needs more room than the descriptive
# headers before truncation would break the fetch.
MAX_LOCATION_CHARS = 512


def _ssdp_msearch(target: tuple, st: str = "ssdp:all", mx: int = 1) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {target[0]}:{target[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {st}\r\n\r\n"
    ).encode()


def _parse_ssdp_headers(data: bytes) -> dict:
    out = {}
    for line in data.decode("utf-8", "replace").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip().lower()[:64]
            out[k] = _safe(v, MAX_LOCATION_CHARS if k == "location" else MAX_HEADER_CHARS)
    return out


MAX_DESC_BYTES = 262144


def _safe_location(location: str, expect_ip: str) -> bool:
    """A device controls its own LOCATION header, so only follow it when it is
    plain http(s) pointing back at the device that answered. Without this a
    device could hand us file:///... or an unrelated internal URL."""
    try:
        u = urlparse(location)
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").strip("[]")
    return bool(host) and (not expect_ip or host == expect_ip)


def _fetch_upnp_description(location: str, expect_ip: str = "", timeout: float = 3.0) -> dict:
    """GET the UPnP device description XML and pull the friendly identity.
    Restricted to http(s) on the responding device, with a bounded response."""
    if not _safe_location(location, expect_ip):
        return {}
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", str(timeout),
             "--proto", "=http,https", "--proto-redir", "=http,https",
             "--max-redirs", "2", "--max-filesize", str(MAX_DESC_BYTES),
             location],
            capture_output=True, text=True, timeout=timeout + 1, check=False,
        ).stdout[:MAX_DESC_BYTES]
    except (OSError, subprocess.TimeoutExpired):
        return {}
    d = {}
    # A lazy `<tag>(.*?)</tag>` restarts at every opening tag, so a body of
    # 256 KB of unclosed <friendlyName> costs a minute of CPU on the worker
    # thread and hands the device back the control the SSDP deadline took away.
    # Scanning for the first opener and the first closer after it picks exactly
    # the same span in linear time.
    low = out.lower()
    for tag in ("friendlyName", "manufacturer", "modelName", "modelNumber", "deviceType"):
        opener, closer = f"<{tag.lower()}>", f"</{tag.lower()}>"
        start = low.find(opener)
        end = low.find(closer, start + len(opener)) if start >= 0 else -1
        if end < 0:
            continue
        d[tag] = _safe(out[start + len(opener):end], 120)
    return d


@dataclass
class Upnp:
    server: str = ""
    device_type: str = ""
    friendly_name: str = ""
    manufacturer: str = ""
    model: str = ""


def ssdp_query(ip: str, timeout: float = 2.0) -> Optional[Upnp]:
    """Unicast M-SEARCH to one host; follow its description URL if it answers."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(_ssdp_msearch((ip, 1900)), (ip, 1900))
        server = location = dtype = ""
        deadline = time.monotonic() + timeout
        try:
            for _ in range(MAX_SSDP_REPLIES):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                s.settimeout(left)
                data, _ = s.recvfrom(4096)
                h = _parse_ssdp_headers(data)
                server = server or h.get("server", "")
                location = location or h.get("location", "")
                dtype = dtype or h.get("st", "")
                if location:
                    break
        except socket.timeout:
            pass
        finally:
            s.close()
        if not (server or location):
            return None
        up = Upnp(server=server, device_type=dtype)
        if location:
            desc = _fetch_upnp_description(location, expect_ip=ip)
            up.friendly_name = desc.get("friendlyName", "")
            up.manufacturer = desc.get("manufacturer", "")
            up.model = " ".join(x for x in (desc.get("modelName", ""), desc.get("modelNumber", "")) if x)
            if desc.get("deviceType"):
                up.device_type = desc["deviceType"]
        return up
    except OSError:
        return None


def ssdp_discover(timeout: float = 3.0) -> dict:
    """Multicast M-SEARCH; return {ip: Upnp-ish dict} for every responder."""
    found: dict[str, dict] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        s.sendto(_ssdp_msearch(_SSDP_MCAST), _SSDP_MCAST)
        deadline = time.monotonic() + timeout
        try:
            for _ in range(MAX_SSDP_REPLIES):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                s.settimeout(left)
                data, addr = s.recvfrom(4096)
                h = _parse_ssdp_headers(data)
                ip = addr[0]
                e = found.setdefault(ip, {"server": "", "types": set(), "location": ""})
                e["server"] = e["server"] or h.get("server", "")
                e["location"] = e["location"] or h.get("location", "")
                if h.get("st") and len(e["types"]) < MAX_SSDP_TYPES:
                    e["types"].add(h["st"])
        except socket.timeout:
            pass
        finally:
            s.close()
    except OSError:
        return {}
    for e in found.values():
        e["types"] = sorted(e["types"])
    return found


# --------------------------------------------------------------------------- #
# NetBIOS name service (UDP 137)
# --------------------------------------------------------------------------- #

def _nb_encode16(raw16: bytes) -> bytes:
    """First-level encode an exact 16-byte NetBIOS name."""
    out = bytearray()
    for ch in raw16[:16].ljust(16, b"\x00"):
        out.append((ch >> 4) + ord("A"))
        out.append((ch & 0x0F) + ord("A"))
    return bytes(out)


@dataclass
class NetBIOS:
    name: str = ""
    workgroup: str = ""
    is_server: bool = False
    names: list = field(default_factory=list)


def netbios_name(ip: str, timeout: float = 1.5) -> Optional[NetBIOS]:
    """Node-status (NBSTAT) query for the workstation name and workgroup."""
    tid = 0x4E53
    header = struct.pack(">HHHHHH", tid, 0x0010, 1, 0, 0, 0)  # broadcast flag set
    question = b"\x20" + _nb_encode16(b"*" + b"\x00" * 15) + b"\x00" + struct.pack(">HH", 0x0021, 0x0001)
    pkt = header + question
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(pkt, (ip, 137))
        data, _ = s.recvfrom(2048)
        s.close()
    except (OSError, socket.timeout):
        return None
    # skip header(12) + question echo(name + 4). Answer: name(34?) ... find rdata.
    try:
        if len(data) < 12:
            return None
        qdcount, ancount = struct.unpack(">HH", data[4:8])
        if ancount < 1:
            return None
        idx = 12
        # a reply may or may not echo the question; honour qdcount
        for _ in range(qdcount):
            qlen = data[idx]
            idx += 1 + qlen + 1   # length + label + terminating null
            idx += 4              # qtype + qclass
        # answer RR
        # answer name is usually a pointer (0xC0 0x0C) = 2 bytes, or repeated name
        if data[idx] & 0xC0 == 0xC0:
            idx += 2
        else:
            alen = data[idx]; idx += 1 + alen + 1
        idx += 2 + 2 + 4     # type + class + ttl
        rdlen = struct.unpack(">H", data[idx:idx+2])[0]; idx += 2
        num = data[idx]; idx += 1
        nb = NetBIOS()
        for _ in range(num):
            raw = _safe(data[idx:idx+15].decode("ascii", "replace"), 15)
            suffix = data[idx+15]
            flags = struct.unpack(">H", data[idx+16:idx+18])[0]
            group = bool(flags & 0x8000)
            idx += 18
            nb.names.append(f"{raw}<{suffix:02x}>{'(g)' if group else ''}")
            if suffix == 0x00 and not group and not nb.name:
                nb.name = raw
            if suffix == 0x20:
                nb.is_server = True
            if group and suffix in (0x00, 0x1C, 0x1E) and not nb.workgroup and raw != nb.name:
                nb.workgroup = raw
        return nb if (nb.name or nb.names) else None
    except (IndexError, struct.error):
        return None


# --------------------------------------------------------------------------- #
# SNMP sysDescr (UDP 161, public community)
# --------------------------------------------------------------------------- #

def _asn_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _asn_len(len(value)) + value


def snmp_sysdescr(ip: str, community: str = "public", timeout: float = 1.5) -> str:
    """SNMP v1 GET of sysDescr.0 (1.3.6.1.2.1.1.1.0)."""
    oid = bytes([0x06, 0x08, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x01, 0x01, 0x00])
    varbind = _tlv(0x30, oid + _tlv(0x05, b""))          # OID + NULL
    vblist = _tlv(0x30, varbind)
    pdu = _tlv(0xA0, _tlv(0x02, bytes([0x01]))           # request-id
                     + _tlv(0x02, bytes([0x00]))          # error-status
                     + _tlv(0x02, bytes([0x00]))          # error-index
                     + vblist)
    msg = _tlv(0x30, _tlv(0x02, bytes([0x00]))            # version v1
                     + _tlv(0x04, community.encode())     # community
                     + pdu)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(msg, (ip, 161))
        data, _ = s.recvfrom(2048)
        s.close()
    except (OSError, socket.timeout):
        return ""
    # find the response OCTET STRING that follows the sysDescr OID
    pos = data.find(oid)
    if pos < 0:
        return ""
    i = pos + len(oid)
    if i >= len(data) or data[i] != 0x04:  # OCTET STRING
        return ""
    i += 1
    if i >= len(data):
        return ""
    ln = data[i]
    i += 1
    if ln & 0x80:
        nby = ln & 0x7F
        if nby == 0 or i + nby > len(data):
            return ""
        ln = int.from_bytes(data[i:i + nby], "big")
        i += nby
    if ln < 0 or i > len(data):
        return ""
    return _safe(data[i:i + ln].decode("utf-8", "replace"), 200)


# --------------------------------------------------------------------------- #
# aggregate + device-type guess
# --------------------------------------------------------------------------- #

@dataclass
class Discovery:
    upnp: Optional[Upnp] = None
    netbios: Optional[NetBIOS] = None
    snmp: str = ""


def enrich(ip: str, progress=None, stop=None) -> Discovery:
    """`stop` is an optional threading.Event: three UDP round trips is long
    enough that a caller who has closed its window wants the rest skipped."""
    def say(m):
        if progress:
            progress(m)

    def stopped():
        return stop is not None and stop.is_set()

    d = Discovery()
    if stopped():
        return d
    say("upnp/ssdp")
    d.upnp = ssdp_query(ip)
    if stopped():
        return d
    say("netbios")
    d.netbios = netbios_name(ip)
    if stopped():
        return d
    say("snmp")
    d.snmp = snmp_sysdescr(ip)
    return d
