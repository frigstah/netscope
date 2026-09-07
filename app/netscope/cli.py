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

from . import core, store, notify, assess, discover, recon, report


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
    st["inventory"] = store.summary()
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
    inv = st["inventory"]
    if inv.get("seeded"):
        print(f"  inventory {inv['total']} known, {inv['present']} present, "
              f"{inv['unknown']} unknown, {inv['trusted']} trusted")
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
    events = store.record_sweep(result.hosts, result.network)
    notify.notify_events(events)
    if as_json:
        out = result.to_json()
        out["events"] = events
        print(json.dumps(out))
        return 0
    for h in result.hosts:
        flag = "SELF" if h.is_self else ("GW" if h.is_gateway else "")
        rtt = f"{h.rtt_ms:.1f}ms" if h.rtt_ms >= 0 else "arp"
        print(f"  {h.ip:<16} {flag:<4} {h.mac:<18} {rtt:>9}  {h.label:<28} {h.vendor}")
    for e in events:
        print(f"  * {e['type']:<11} {e.get('ip',''):<16} {e.get('name','')}", file=sys.stderr)
    print(f"{len(result.hosts)} hosts in {result.duration:.1f}s "
          f"({len(events)} change{'s' if len(events)!=1 else ''})", file=sys.stderr)
    return 0


def cmd_watch(network: str, interval: int) -> int:
    """Sweep on an interval, record changes, and fire desktop notifications.
    Meant to run headless (a systemd --user service); Ctrl-C to stop."""
    iface = ""
    if not network:
        network, iface = _default_network()
    if not network:
        print("no network to watch", file=sys.stderr)
        return 1
    interval = max(15, interval)
    print(f"{core.APP_NAME}: watching {network} every {interval}s "
          f"(first sweep seeds the baseline)", file=sys.stderr)
    try:
        while True:
            try:
                result = core.sweep(network, iface, resolve_names=True)
                events = store.record_sweep(result.hosts, result.network)
                notify.notify_events(events)
                stamp = time.strftime("%H:%M:%S")
                if events:
                    for e in events:
                        print(f"[{stamp}] {e['type']} {e.get('ip','')} {e.get('name','')}",
                              file=sys.stderr)
                else:
                    print(f"[{stamp}] {len(result.hosts)} hosts, no change", file=sys.stderr)
            except Exception as e:  # never let one bad sweep kill the watcher
                print(f"watch: sweep failed: {e}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("watch stopped", file=sys.stderr)
        return 0


def cmd_inventory(as_json: bool) -> int:
    devices = sorted(store.load().values(),
                     key=lambda d: (not d.present, not d.trusted, d.ip))
    if as_json:
        from dataclasses import asdict as _asdict
        print(json.dumps([_asdict(d) for d in devices]))
        return 0
    for d in devices:
        flags = "".join([
            "P" if d.present else ".",
            "T" if d.trusted else ("S" if d.is_self else ("G" if d.is_gateway else "?")),
            "R" if d.randomized else " ",
        ])
        print(f"  {flags}  {d.ip:<16} {d.mac:<18} {d.display_name:<28} {d.vendor}")
    print(f"{len(devices)} devices ("
          f"{sum(1 for d in devices if d.unknown and d.present)} unknown present)",
          file=sys.stderr)
    return 0


def cmd_events(as_json: bool, limit: int) -> int:
    evs = store.recent_events(limit)
    if as_json:
        print(json.dumps(evs))
        return 0
    for e in reversed(evs):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("ts", 0)))
        print(f"  {stamp}  {e['type']:<12} {e.get('ip',''):<16} "
              f"{e.get('name','')} {e.get('detail','')}")
    return 0


def cmd_probe(ip: str, ports: str, as_json: bool) -> int:
    plist = core.profile_ports(ports)
    if not plist:
        print("no ports", file=sys.stderr)
        return 1
    if not as_json:
        print(f"probing {ip} ({len(plist)} ports) ...", file=sys.stderr)
    hits = core.probe(ip, plist)
    # find the host's MAC from the last sweep so the probe attaches to its device
    mac = ""
    scan = core.last_scan() or {}
    for h in scan.get("hosts", []):
        if h.get("ip") == ip:
            mac = h.get("mac", "")
            break
    events = store.record_probe(ip, mac, hits)
    notify.notify_events(events)
    findings, score, lvl = assess.assess(hits)
    store.update_device(store.device_key(mac, ip), risk=score)
    if as_json:
        print(json.dumps({"ports": [asdict(h) for h in hits],
                          "assessment": {"score": score, "level": lvl,
                                         "findings": assess.findings_json(findings)}}))
        return 0
    for h in hits:
        print(f"  {h.port:>5}/tcp  {h.service:<16} {h.banner}")
    print(f"{len(hits)} open · {assess.summary_line(findings, score)}", file=sys.stderr)
    for f in findings:
        if f.severity != "info":
            print(f"  [{f.severity:<6}] {f.title}: {f.detail}", file=sys.stderr)
    return 0


def cmd_report(fmt: str, out: str, sanitized: bool) -> int:
    try:
        text = report.build(fmt, sanitized=sanitized)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if out:
        try:
            with open(out, "w") as f:
                f.write(text)
        except OSError as e:
            print(f"could not write {out}: {e}", file=sys.stderr)
            return 1
        print(f"wrote {out}", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_wake(mac: str) -> int:
    ok = core.wake_on_lan(mac)
    print(f"wake-on-lan {'sent to' if ok else 'failed for'} {mac}", file=sys.stderr)
    return 0 if ok else 1


def cmd_wifi(as_json: bool) -> int:
    w = core.wifi_status()
    if as_json:
        print(json.dumps(w))
        return 0
    if not w:
        print("not connected to Wi-Fi", file=sys.stderr)
        return 1
    print(f"  SSID     {w['ssid']}  ({w.get('security','')})")
    print(f"  signal   {w.get('signal_pct',-1)}%  {w.get('signal_dbm',0)} dBm")
    print(f"  link     {w.get('band','')}  ch {w.get('channel','')}  {w.get('rate','')}")
    if w.get("bssid"):
        print(f"  bssid    {w['bssid']}")
    return 0


def cmd_identify(ip: str, as_json: bool) -> int:
    disc = discover.enrich(ip)
    # a light dossier for the type guess (no full probe; use ARP/known ports)
    from dataclasses import asdict as _asdict
    hits = core.probe(ip, core.profile_ports("quick"))
    mac = (core.neighbours().get(ip) or {}).get("mac", "")
    d = recon.Dossier(ip=ip, mac=mac, vendor=core.vendor_for_mac(mac),
                      hostname=core._rdns(ip) or core._mdns(ip),
                      ports=hits, discovery=disc, services=recon._mdns_services(ip))
    dtype = recon.guess_type(d)
    out = {"ip": ip, "dtype": dtype,
           "upnp": _asdict(disc.upnp) if disc.upnp else None,
           "netbios": _asdict(disc.netbios) if disc.netbios else None,
           "snmp": disc.snmp}
    if as_json:
        print(json.dumps(out))
        return 0
    print(f"{ip}  device type: {dtype}")
    if disc.upnp:
        u = disc.upnp
        print(f"  upnp     {u.friendly_name or u.server}"
              + (f"  ·  {u.model}" if u.model else "")
              + (f"  ·  {u.device_type}" if u.device_type else ""))
    if disc.netbios and (disc.netbios.name or disc.netbios.workgroup):
        nb = disc.netbios
        print(f"  netbios  {nb.name}  workgroup={nb.workgroup}" + ("  [server]" if nb.is_server else ""))
    if disc.snmp:
        print(f"  snmp     {disc.snmp}")
    if not (disc.upnp or (disc.netbios and disc.netbios.name) or disc.snmp):
        print("  (no UPnP/NetBIOS/SNMP response — guess is from vendor + open ports)")
    return 0


def cmd_assess(ip: str, ports: str, as_json: bool) -> int:
    plist = core.profile_ports(ports)
    hits = core.probe(ip, plist)
    findings, score, lvl = assess.assess(hits)
    if as_json:
        print(json.dumps({"ip": ip, "score": score, "level": lvl,
                          "findings": assess.findings_json(findings)}))
        return 0
    print(f"{ip}  {assess.summary_line(findings, score)}  [{lvl}]")
    for f in findings:
        print(f"  [{f.severity:<6}] {f.title}: {f.detail}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="netscope", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--scan", nargs="?", const="", metavar="CIDR")
    ap.add_argument("--probe", metavar="IP")
    ap.add_argument("--assess", metavar="IP", help="probe then report the security posture")
    ap.add_argument("--identify", metavar="IP", help="active discovery (UPnP/NetBIOS/SNMP) + device-type guess")
    ap.add_argument("--wifi", action="store_true", help="current Wi-Fi link details")
    ap.add_argument("--report", metavar="FMT", help="export a report: json|csv|md|html")
    ap.add_argument("--out", metavar="FILE", help="write the report to a file")
    ap.add_argument("--sanitized", action="store_true", help="mask public IP/location in the report")
    ap.add_argument("--wake", metavar="MAC", help="send a Wake-on-LAN magic packet")
    ap.add_argument("--tui", action="store_true", help="terminal UI")
    ap.add_argument("--watch", nargs="?", const="", metavar="CIDR",
                    help="sweep on an interval and notify on changes")
    ap.add_argument("--interval", type=int, default=120, help="watch interval seconds")
    ap.add_argument("--inventory", action="store_true", help="list remembered devices")
    ap.add_argument("--events", action="store_true", help="recent change events")
    ap.add_argument("--limit", type=int, default=50)
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
    if args.watch is not None:
        return cmd_watch(args.watch, args.interval)
    if args.inventory:
        return cmd_inventory(args.json)
    if args.events:
        return cmd_events(args.json, args.limit)
    if args.scan is not None:
        return cmd_scan(args.scan, args.json)
    if args.report:
        return cmd_report(args.report, args.out or "", args.sanitized)
    if args.wake:
        return cmd_wake(args.wake)
    if args.wifi:
        return cmd_wifi(args.json)
    if args.identify:
        return cmd_identify(args.identify, args.json)
    if args.assess:
        return cmd_assess(args.assess, args.ports, args.json)
    if args.probe:
        return cmd_probe(args.probe, args.ports, args.json)

    if args.tui:
        from . import tui
        return tui.main()
    from . import gui
    return gui.main()


if __name__ == "__main__":
    sys.exit(main())
