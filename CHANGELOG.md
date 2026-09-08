# Changelog

Notable changes, newest first.

## [1.9.0] - 2026-09-08

Marketplace-readiness pass against the Omarchy plugin development guide.

### Fixed
- The bar widget polled `netscope --status --json` every two minutes forever,
  open or closed, which spawned a process inside the long-running shell and
  refreshed the third-party public-IP lookup every ten minutes for as long as
  the session lasted. With the popout closed it now does nothing at all unless
  "Tint icon on unknown device" is on, and that poll uses `--no-public`, so an
  idle bar never contacts anyone.
- The popout's copy action built a shell command out of text read off the
  network. It passes argv to `wl-copy` directly now, with no shell involved,
  which matters because plugins run unsandboxed with your permissions.
- A failed status or sweep left the widget showing stale data as though nothing
  had happened; failures now surface in the header.
- `public` is a reserved word to older QML parsers, so the status parse is
  written as `d["public"]`.

### Added
- `netscope --status --no-public` skips the third-party public-IP lookup.

### Changed
- Documented every external binary the plugin can invoke (`wl-clipboard`,
  `xdg-utils`, `openssh`, `libnotify`, `avahi-browse`), all four bar-widget
  settings with their types and defaults, what the widget does while closed,
  and the AI privilege boundary.
- Uninstall now covers the non-interactive `--yes` and refreshes the desktop
  and icon caches that `install.sh` updates.
- Dropped `docs/netscope.png`, a duplicate of the preview screenshot.

## [1.8.0] - 2026-09-08

A second audit pass. Every fix below was reproduced before and after.

### Security and privacy
- The whole-network AI briefing no longer carries your public IP, ISP or city.
  It states that public connectivity exists and withholds the address.
- Untrusted device text is filtered on what is printable rather than on a
  control-character range, so an invisible right-to-left override or a
  zero-width space in a device name can no longer reach your terminal, a report
  or an AI prompt. Ordinary Unicode names are untouched.
- SSDP, NetBIOS and SNMP replies are length-capped and stripped at the source,
  and SSDP has an overall deadline so a chatty device cannot pin a worker.
- `--sanitized` also masks the interface a device was seen on.

### Added
- `NETSCOPE_NOTIFY=0` mutes desktop alerts for a run. Worth setting when you
  script `--scan` in a loop or point NetScope at a fresh state directory, since
  an empty inventory makes every device on the network look new at once.
- A sweep that hits the address cap now says so, in the window, the CLI and the
  TUI. Devices past the cap are never contacted, so a partial sweep is not a
  clean bill of health.
- Watch mode in the TUI actually alerts: desktop notifications plus an on-screen
  ALERTS panel for when there is no notification daemon.

### Fixed
- An AI investigation could hang forever on "fingerprinting device": the abort
  path raised an exception class that was never defined, which killed the worker
  thread silently and left the window waiting.
- Closing the AI window left its blink timer running and its fingerprint worker
  driving a destroyed window.
- Every log line and AI stream chunk leaked a GtkTextMark, and the report buffer
  had no ceiling, so a long investigation slowed the window down without bound.
- A device that answers ping but has no ARP entry no longer splits into a second
  inventory record, which used to fire new / gone / back for one device and
  orphan the name and trust you had set.
- IPv6 devices are aged only by a sweep on the interface they were seen on, so
  sweeping one network no longer reports v6 devices on another as departed.
- Probing with an empty port list no longer wipes a device's known ports.
- A corrupt or hand-edited inventory is coerced to the right field types on
  load instead of failing later while rendering a report.
- The TUI notified on every sweep and probe rather than only while watching.
- Notification counts no longer include alerts the daemon rejected.
- AI engines are cleaned up with the temporary directory they were given, and
  leftover helper processes are killed by verified group membership rather than
  by signalling a group id that may no longer be ours.
- `--scan` and `--watch` reject a bad or IPv6 CIDR cleanly instead of raising;
  `--assess` refuses an empty port spec instead of reporting "clean"; an empty
  option value is a usage error rather than a silent GUI launch; `--identify`
  stores the device type it worked out.
- The UPnP rule no longer fires on TCP 5000 / 49152 / 49153, which the same
  code classifies as HTTP, and no longer reports port 0.
- `bin/netscope` resolves the app from its own location, so the symlink
  `install.sh` creates runs the checkout it belongs to.

### Changed
- Requirements corrected to Python 3.11+ and GTK 4.12+, which is what the code
  has always needed (`tomllib`, `ColumnView.scroll_to`).
- `install.sh` warns instead of failing when the GTK stack is absent, since the
  CLI, `--tui` and the watcher do not need it, and it no longer claims AI SCAN
  needs a cloud CLI when a local Ollama will do.
- Uninstall instructions cover the installed icon and the cache directory.

## [1.7.0] - 2026-09-08

Security, privacy and correctness pass ahead of a public release.

### Security
- A device controls its own UPnP `LOCATION`, and it was fetched unrestricted, so
  a device on the LAN could point NetScope at `file:///...` (or an unrelated
  host) and have that content become its "identity" - and then AI evidence.
  Descriptions are now only fetched over http(s), only from the device that
  answered, with redirects and response size bounded.
- CSV exports neutralize spreadsheet formula injection: a device name off the
  network beginning with `=`, `+`, `-` or `@` is no longer run as a formula.

### Privacy
- The AI window no longer starts a request before the engine is settled. With
  both a cloud CLI and a local Ollama available it waits for a choice and
  remembers it, so nothing reaches a cloud provider by default.
- `--sanitized` reports now mask every globally-routable address (including
  IPv6 and addresses inside events), hostnames and MAC suffixes. Private LAN
  addresses are kept.
- Documented the automatic third-party public-IP and location lookup.

### Fixed
- AI cancellation kills the whole process group, so STOP works even when the
  CLI spawned helpers holding its pipes; RERUN cancels the previous run instead
  of interleaving two reports.
- Inventory survives a damaged file (wrong shape or invalid JSON is quarantined,
  not silently dropped).
- Learning a device's MAC no longer creates a second record and orphan its name
  and trust setting.
- IPv6-only devices are now aged toward "gone"; probing a narrower port range no
  longer discards the wider baseline and then reports old ports as new.
- SNMP replies are bounds-checked, NetBIOS parsing honours the header counts,
  IPv6 web/HTTP URLs are bracketed, and an SSID containing ":" no longer shifts
  the Wi-Fi fields.

### Changed
- Removed the unused `grim` dependency; documented minimum Python/GTK versions;
  corrected the watch and icon-tinting claims in the README.

## [1.6.3] - 2026-09-08

### Changed
- The bar icon now follows the theme colour by default. A new "Tint icon on
  unknown device" setting (off by default) makes it turn urgent while an
  untrusted device is present.
- Replaced em dashes with hyphens throughout the docs and UI text.

## [1.6.2] - 2026-09-08

### Fixed
- Notification flood: devices with randomized/private MACs (phones, some IoT)
  rejoin under a new MAC each sweep and looked "new" every time; they no longer
  raise device-joined/left desktop notifications.
- Marking a device trusted without typing a name no longer crashes.
- Concurrency: inventory writes are serialized across GUI threads and the
  headless watcher (file lock + atomic temp-file writes, no leftover .tmp).
- Presence: a device is only aged toward "gone" when its own subnet was
  actually swept, so scanning a different range no longer marks it gone.
- Aborted sweeps no longer overwrite the cached last scan; the GUI records a
  probe's ports/risk to the inventory (was only done from the CLI).
- AI streaming reads stdout and stderr concurrently (no pipe deadlock), stays
  responsive to STOP, and always reaps the process and closes its pipes.
- Sweep builds its target list lazily (large CIDRs) and keeps IPv6-only hosts
  that discovery adds. TUI selection index is clamped against list changes.

## [1.6.1] - 2026-09-08

### Fixed
- Marking a device trusted now updates the row immediately: list cells are
  reactive to property changes (STATUS, name, and flag columns), and the TRUST
  toggle shows a clear checked state.
- The device STATUS column (UNKN / TRUST / NEW) is now labeled so the "unknown"
  status is self-explanatory.

### Changed
- Bar icon renders through OpticalGlyph so it sits at the same height as the
  other bar icons; a new "Bar icon" setting picks the glyph (access-point,
  radar, wifi, crosshairs, lan) - default access-point.

## [1.6.0] - 2026-09-08

Reports, actions, and a terminal UI.

### Added
- `report.py`: export the inventory as JSON, CSV, Markdown, or a self-contained
  HTML page. GUI `ctrl+E`, CLI `netscope --report FMT [--out FILE] [--sanitized]`.
- `actions.py`: per-device `⋯` menu in the window - open web UI, SSH, Wake-on-LAN
  (`core.wake_on_lan`, `netscope --wake MAC`), ping, copy IP.
- `tui.py`: a dependency-free curses terminal UI (`netscope --tui`) with
  interfaces, Wi-Fi, public IP, a live host list, sweep, probe and watch.

## [1.5.0] - 2026-09-08

IPv6 and Wi-Fi context.

### Added
- Wi-Fi panel in the sidebar (SSID, signal %/dBm, band, channel, rate,
  security) from nmcli + iw; `netscope --wifi` and a `wifi` field in `--status`.
- IPv6: sweeps read the v6 neighbour table (ping ff02::1), attach global v6
  addresses to hosts by MAC, add v6-only neighbours, and tag hosts `+v6`.

## [1.4.0] - 2026-09-08

Device identification.

### Added
- `discover.py`: unprivileged active discovery - SSDP/UPnP (friendly name,
  model, device type), NetBIOS name query, and SNMP `sysDescr` (public).
- A device-type heuristic (router, NAS, camera, printer, media, IoT, VM,
  file server, …) combining discovery with vendor and open ports.
- Discovery and the type guess are folded into the AI fingerprint and stored
  on the device record. New CLI `netscope --identify IP`.

## [1.3.0] - 2026-09-08

Local AI and whole-network summary.

### Added
- Local **Ollama** engine for AI SCAN (http://localhost:11434), so an
  investigation can run fully on-device. The AI window has an engine selector
  (cloud CLI or local) and a RERUN button. `NETSCOPE_OLLAMA_HOST` /
  `NETSCOPE_OLLAMA_MODEL` override the endpoint and model.
- Whole-network AI summary: click the unknown-count badge or press
  `ctrl+shift+I` for one report that inventories the LAN, ranks risks, and
  recommends fixes, built from the stored inventory and per-device findings.

## [1.2.0] - 2026-09-08

Security posture.

### Added
- `assess.py`: a rules engine that grades a probed device's exposure and
  returns findings plus a 0-100 risk score (plaintext admin, unauthenticated
  databases, risky management ports, UPnP, broad open-port surface).
- GUI: a `RISK N` badge and a findings popover appear after each probe; the
  score is stored on the device record.
- CLI `--assess IP`, and `--probe --json` now includes the assessment.
- The findings are included in the AI investigation prompt so the model
  corroborates rather than invents.

## [1.1.0] - 2026-09-08

Continuous monitoring.

### Added
- Persistent device inventory in `~/.local/state/netscope/`, keyed by MAC:
  remembers every device, tracks presence, and detects randomized MACs.
- Name devices and mark them trusted from the probe row; untrusted present
  devices show an `UNKN` mark and a `⚠ N` count badge, newly-seen devices `NEW`.
- WATCH toggle: keeps sweeping and fires desktop notifications when an unknown
  device joins or a new open port appears on a known host.
- Headless watcher (`netscope --watch`) and a systemd --user service
  (`install.sh --watch`) so alerts fire with the window closed.
- New CLI: `--watch`, `--inventory`, `--events`; `--scan`/`--status` now record
  and report the inventory. The bar icon tints while unknown devices are present.

## [1.0.0] - 2026-09-07

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
