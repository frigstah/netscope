"""NetScope command line. Developed for and by frig.

  netscope                 open the GUI
  netscope --status        interfaces, public ip, last scan (add --json for machines)
  netscope --scan [CIDR]   sweep the LAN (default: the default-route network)
  netscope --probe IP      probe a host (--ports quick|common|full|22,80,8000-8100)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from . import core


def _default_network() -> tuple[str, str]:
    for i in core.interfaces():
        if i.is_default and i.networks:
            return i.networks[0], i.name
    for i in core.interfaces():
        if i.up and i.kind != "loopback" and i.networks:
            return i.networks[0], i.name
    return "", ""


def cmd_status(as_json: bool, refresh: bool) -> int:
    st = core.status(refresh_public=refresh)
    if as_json:
        print(json.dumps(st))
        return 0
    print(f"{core.APP_NAME} v{core.VERSION} // {core.TAGLINE}")
    print()
    for i in st["interfaces"]:
        tag = "DEFAULT" if i["default"] else i["state"]
        v4 = ", ".join(i["ipv4"]) or "-"
        print(f"  {i['name']:<12} {tag:<8} {v4}")
    p = st["public"]
    print()
    print(f"  public   {p.get('ipv4') or p.get('error') or '-'}  {p.get('org', '')}  {p.get('city', '')} {p.get('country', '')}")
    ls = st["lastScan"]
    if ls["hosts"]:
        age = int(time.time() - ls["at"])
        print(f"  lastscan {ls['hosts']} hosts on {ls['network']} ({age}s ago)")
    return 0


def cmd_scan(network: str, as_json: bool) -> int:
    iface = ""
    if not network:
        network, iface = _default_network()
    if not network:
        print("no network to sweep", file=sys.stderr)
        return 1
    if not as_json:
        print(f"sweeping {network} ...", file=sys.stderr)
    result = core.sweep(network, iface)
    if as_json:
        print(json.dumps(result.to_json()))
        return 0
    for h in result.hosts:
        flag = "SELF" if h.is_self else ("GW" if h.is_gateway else "")
        rtt = f"{h.rtt_ms:.1f}ms" if h.rtt_ms >= 0 else "arp"
        print(f"  {h.ip:<16} {flag:<4} {h.mac:<18} {rtt:>9}  {h.label:<28} {h.vendor}")
    print(f"{len(result.hosts)} hosts in {result.duration:.1f}s", file=sys.stderr)
    return 0


def cmd_probe(ip: str, ports: str, as_json: bool) -> int:
    plist = core.profile_ports(ports)
    if not plist:
        print("no ports", file=sys.stderr)
        return 1
    if not as_json:
        print(f"probing {ip} ({len(plist)} ports) ...", file=sys.stderr)
    hits = core.probe(ip, plist)
    if as_json:
        print(json.dumps([asdict(h) for h in hits]))
        return 0
    for h in hits:
        print(f"  {h.port:>5}/tcp  {h.service:<16} {h.banner}")
    print(f"{len(hits)} open", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="netscope", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--scan", nargs="?", const="", metavar="CIDR")
    ap.add_argument("--probe", metavar="IP")
    ap.add_argument("--ports", default="quick")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="bypass the public-ip cache")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print(f"{core.APP_NAME} {core.VERSION} // {core.TAGLINE}")
        return 0
    if args.status:
        return cmd_status(args.json, args.refresh)
    if args.scan is not None:
        return cmd_scan(args.scan, args.json)
    if args.probe:
        return cmd_probe(args.probe, args.ports, args.json)

    from . import gui
    return gui.main()


if __name__ == "__main__":
    sys.exit(main())
