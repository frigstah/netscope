"""Security posture assessment. Developed for and by frig.

Turns a set of open ports (and, when available, banners and HTTP responses)
into plain, factual findings an owner should know about, plus a 0-100 risk
score. Awareness only: it describes exposure, never how to exploit it.

Works off a list of core.PortHit; pass an optional recon.Dossier for the
extra HTTP/auth signal.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

# severity -> score weight and label
SEV = {"high": 30, "medium": 15, "low": 6, "info": 2}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


@dataclass
class Finding:
    severity: str          # high | medium | low | info
    title: str
    detail: str
    port: int = 0


# Plaintext / cleartext-credential services: anything carrying logins or data
# in the clear on a LAN.
_PLAINTEXT = {
    23: ("Telnet", "remote shell with no encryption; credentials cross the network in clear text"),
    21: ("FTP", "file transfer in clear text unless FTPS is enforced"),
    69: ("TFTP", "unauthenticated file transfer"),
    512: ("rexec", "legacy remote execution in clear text"),
    513: ("rlogin", "legacy remote login in clear text"),
    514: ("rsh", "legacy remote shell in clear text"),
    110: ("POP3", "mail retrieval in clear text unless TLS is used"),
    143: ("IMAP", "mail access in clear text unless TLS is used"),
    25: ("SMTP", "mail transfer, often unauthenticated on a LAN"),
    161: ("SNMP", "device management, frequently left on the 'public' community string"),
    389: ("LDAP", "directory access in clear text unless LDAPS is used"),
    1883: ("MQTT", "IoT message bus, often without auth or TLS"),
    5900: ("VNC", "remote desktop, weak or no auth in many setups"),
}

# Databases and stores that are dangerous when reachable from the LAN, as they
# are commonly deployed with no authentication.
_DATASTORES = {
    6379: ("Redis", "in-memory datastore, unauthenticated by default"),
    27017: ("MongoDB", "database, historically unauthenticated by default"),
    9200: ("Elasticsearch", "search cluster, no auth in the default config"),
    9300: ("Elasticsearch transport", "cluster transport port exposed"),
    11211: ("Memcached", "cache with no auth; also a DDoS reflector if UDP is open"),
    5432: ("PostgreSQL", "database reachable on the network"),
    3306: ("MySQL/MariaDB", "database reachable on the network"),
    1433: ("MSSQL", "database reachable on the network"),
    5984: ("CouchDB", "database with a history of open-by-default installs"),
    2379: ("etcd", "cluster key-value store, often unauthenticated"),
    8086: ("InfluxDB", "time-series database reachable on the network"),
    7474: ("Neo4j", "graph database reachable on the network"),
    9042: ("Cassandra", "database reachable on the network"),
}

# Remote-management / exposure surfaces worth calling out.
_MGMT = {
    3389: ("RDP", "Windows remote desktop exposed on the network"),
    5985: ("WinRM", "Windows remote management (HTTP)"),
    5986: ("WinRM", "Windows remote management (HTTPS)"),
    445: ("SMB", "Windows/Samba file sharing; a classic lateral-movement surface"),
    139: ("NetBIOS", "legacy Windows file sharing"),
    2375: ("Docker API", "unauthenticated Docker daemon = root on the host"),
    4243: ("Docker API", "unauthenticated Docker daemon = root on the host"),
    2376: ("Docker API (TLS)", "Docker daemon exposed over the network"),
    10250: ("Kubelet", "Kubernetes node API, sensitive if unauthenticated"),
    6443: ("Kubernetes API", "cluster control plane exposed"),
    7547: ("TR-069 / CWMP", "ISP remote-management port; a repeated router-CVE surface"),
    9100: ("JetDirect", "raw printing port, unauthenticated by design"),
    515: ("LPD", "line-printer daemon, unauthenticated by design"),
}

_HTTP_ADMIN_HINTS = ("login", "sign in", "admin", "router", "setup", "dashboard",
                     "management", "console", "portal", "unifi", "webui")


def _is_https_port(port: int, service: str) -> bool:
    return port in (443, 8443, 4443, 9443, 8444, 7443, 5986, 8883, 2376) or "https" in service or "tls" in service


def assess(hits: list, dossier: Optional["object"] = None) -> tuple[list, int, str]:
    """Return (findings, score, level). `hits` is a list of core.PortHit."""
    findings: list[Finding] = []
    open_ports = {h.port for h in hits}
    svc = {h.port: (h.service or "") for h in hits}

    for port in sorted(open_ports):
        if port in _PLAINTEXT:
            name, why = _PLAINTEXT[port]
            findings.append(Finding("high" if port in (23, 512, 513, 514) else "medium",
                                    f"{name} exposed", why, port))
        if port in _DATASTORES:
            name, why = _DATASTORES[port]
            findings.append(Finding("high", f"{name} reachable on the LAN", why, port))
        if port in _MGMT:
            name, why = _MGMT[port]
            sev = "high" if port in (2375, 4243, 23, 10250) else "medium"
            findings.append(Finding(sev, f"{name} exposed", why, port))

    # plaintext HTTP admin surface (needs the HTTP probe from a dossier)
    https_seen = any(_is_https_port(p, svc.get(p, "")) for p in open_ports)
    if dossier is not None:
        for w in getattr(dossier, "https", []) or []:
            title = (getattr(w, "title", "") or "").lower()
            looks_admin = any(h in title for h in _HTTP_ADMIN_HINTS)
            if getattr(w, "scheme", "") == "http" and (looks_admin or getattr(w, "auth", "")):
                findings.append(Finding(
                    "medium", "Admin page over plaintext HTTP",
                    f"{w.scheme}://:{w.port} serves a login/management page without TLS"
                    + (f" ({w.title})" if getattr(w, 'title', '') else ""),
                    w.port))
            if getattr(w, "auth", "").lower().startswith("basic"):
                findings.append(Finding(
                    "low", "HTTP Basic auth",
                    f"port {w.port} uses Basic auth; credentials are base64, not encrypted, without TLS",
                    w.port))

    # a bare plaintext web admin without a dossier: 80 open, no HTTPS anywhere
    if dossier is None and 80 in open_ports and not https_seen:
        findings.append(Finding("low", "Plaintext HTTP service",
                                "port 80 is open with no HTTPS seen; if it's an admin UI, logins are unencrypted", 80))

    # UPnP / SSDP exposure
    if open_ports & {1900, 49152, 49153, 5000}:
        findings.append(Finding("low", "UPnP/SSDP surface",
                                "UPnP is exposed; on a router it can let devices open ports to the internet automatically",
                                1900 if 1900 in open_ports else 0))

    # a large number of open ports is itself notable
    if len(open_ports) >= 15:
        findings.append(Finding("low", "Broad open-port surface",
                                f"{len(open_ports)} TCP ports are open; more services means more to keep patched"))

    if not findings:
        findings.append(Finding("info", "Nothing notable",
                                "no plaintext admin, exposed database, or risky management port stood out"))

    findings.sort(key=lambda f: (SEV_ORDER.get(f.severity, 9), f.port))
    score = _score(findings)
    return findings, score, level_for(score)


def _score(findings: list) -> int:
    total = 0
    for f in findings:
        total += SEV.get(f.severity, 0)
    return min(100, total)


def level_for(score: int) -> str:
    if score >= 45:
        return "high"
    if score >= 20:
        return "elevated"
    if score >= 6:
        return "low"
    return "clean"


def summary_line(findings: list, score: int) -> str:
    counts = {}
    for f in findings:
        if f.severity == "info":
            continue
        counts[f.severity] = counts.get(f.severity, 0) + 1
    if not counts:
        return f"risk {score}/100 · clean"
    parts = [f"{n} {sev}" for sev, n in sorted(counts.items(), key=lambda kv: SEV_ORDER[kv[0]])]
    return f"risk {score}/100 · " + ", ".join(parts)


def findings_json(findings: list) -> list:
    return [asdict(f) for f in findings]
