# Changelog

Notable changes, newest first.

## [1.0.0] — 2026-09-07

First release. Developed for and by frig.

### Added
- Bar widget and popout: interfaces with addresses, public IP, and the last
  LAN sweep at a glance. Left click popout, right click opens the window,
  middle click sweeps.
- NetScope window (GTK4 + libadwaita) themed from the current Omarchy colours
  and font: interfaces, public IP with ISP/ASN/geo, a live LAN host sweep
  (ping + ARP, reverse-DNS, mDNS, MAC vendor), and a TCP port probe with
  service names and banners.
- AI SCAN: fingerprints the selected device (ports, banners, mDNS services,
  HTTP titles, TTL) and streams an AI investigation report identifying the
  device and explaining each open port. Uses whichever AI CLI is installed
  (claude, gemini or codex).
- Command line: `netscope --status/--scan/--probe`, each with `--json`.
- Draggable dividers between the interfaces sidebar and the hosts area, and
  between the host list and the probe panel.
