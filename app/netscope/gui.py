"""NetScope GTK4 window. Developed for and by frig."""

from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import asdict
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import actions, ai, assess, core, notify, recon, report, store, theme  # noqa: E402

KIND_GLYPH = {
    "wifi": "󰖩", "ethernet": "󰈀", "vpn": "󰖂", "virtual": "󰡨",
    "usb": "󰕓", "loopback": "󰑙", "other": "󰛳",
}


# --------------------------------------------------------------------------- #
# List models
# --------------------------------------------------------------------------- #

class HostRow(GObject.Object):
    __gtype_name__ = "NetScopeHostRow"
    ip = GObject.Property(type=str, default="")
    mac = GObject.Property(type=str, default="")
    vendor = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    rtt = GObject.Property(type=float, default=-1.0)
    seen = GObject.Property(type=str, default="")
    flag = GObject.Property(type=str, default="")
    mark = GObject.Property(type=str, default="")   # TRUST / NEW / UNKN
    key = GObject.Property(type=str, default="")

    def __init__(self):
        super().__init__()
        self.ipnum = 0

    @classmethod
    def from_host(cls, h: core.Host) -> "HostRow":
        r = cls()
        r.ip = h.ip
        r.mac = h.mac
        r.vendor = h.vendor
        r.name = h.hostname or h.mdns or ""
        r.rtt = h.rtt_ms
        r.seen = "+".join(h.seen_by) + ("+v6" if h.ipv6 else "")
        r.flag = "SELF" if h.is_self else ("GW" if h.is_gateway else ("" if "icmp" in h.seen_by else "ARP"))
        r.key = store.device_key(h.mac, h.ip)
        try:
            r.ipnum = int(ipaddress.ip_address(h.ip))
        except ValueError:
            r.ipnum = 0
        return r

    def update(self, h: core.Host) -> None:
        self.mac = h.mac
        self.vendor = h.vendor
        self.name = h.hostname or h.mdns or ""
        self.rtt = h.rtt_ms
        self.seen = "+".join(h.seen_by)
        self.flag = "SELF" if h.is_self else ("GW" if h.is_gateway else ("" if "icmp" in h.seen_by else "ARP"))


class PortRow(GObject.Object):
    __gtype_name__ = "NetScopePortRow"
    port = GObject.Property(type=int, default=0)
    service = GObject.Property(type=str, default="")
    banner = GObject.Property(type=str, default="")
    latency = GObject.Property(type=float, default=0.0)

    @classmethod
    def from_hit(cls, h: core.PortHit) -> "PortRow":
        r = cls()
        r.port = h.port
        r.service = h.service
        r.banner = h.banner
        r.latency = h.latency_ms
        return r


def _label(css: str = "", xalign: float = 0.0, ellipsize: bool = True) -> Gtk.Label:
    l = Gtk.Label(xalign=xalign)
    if css:
        for c in css.split():
            l.add_css_class(c)
    if ellipsize:
        from gi.repository import Pango
        l.set_ellipsize(Pango.EllipsizeMode.END)
    return l


def _text_column(title: str, attr: str, css: str = "", expand: bool = False,
                 fmt=None, fixed: int = 0) -> Gtk.ColumnViewColumn:
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        item.set_child(_label(css))

    def bind(_f, item):
        obj = item.get_item()
        lbl = item.get_child()
        val = obj.get_property(attr)
        lbl.set_text(fmt(val) if fmt else str(val))

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    col = Gtk.ColumnViewColumn.new(title, factory)
    col.set_expand(expand)
    col.set_resizable(True)
    if fixed:
        col.set_fixed_width(fixed)
    return col


def _copyable(label: Gtk.Label, window: "NetScopeWindow") -> Gtk.Label:
    """Click a value to copy it; keeps the label from grabbing focus the way a selectable label does."""
    label.set_cursor_from_name("pointer")
    label.set_tooltip_text("click to copy")
    g = Gtk.GestureClick()
    g.connect("released", lambda *_: window.copy_text(label.get_text()))
    label.add_controller(g)
    return label


def _dropdown(items: list[str], width: int, cap: int = 16) -> Gtk.DropDown:
    """DropDown whose button label ellipsizes to `cap` chars, so a long item
    (a wide network string) can never balloon the toolbar past the tile."""
    dd = Gtk.DropDown.new_from_strings(items)
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        lbl = _label()
        lbl.set_max_width_chars(cap)
        item.set_child(lbl)

    def bind(_f, item):
        item.get_child().set_text(item.get_item().get_string())

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    dd.set_factory(factory)
    dd.set_size_request(width, -1)
    return dd


def _mark_column() -> Gtk.ColumnViewColumn:
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        box = Gtk.Box(halign=Gtk.Align.START)
        lbl = Gtk.Label()
        lbl.add_css_class("ns-cell-mark")
        box.append(lbl)
        item.set_child(box)

    def bind(_f, item):
        lbl = item.get_child().get_first_child()
        m = item.get_item().mark
        lbl.set_text(m)
        lbl.set_visible(bool(m))
        for c in ("new", "trust", "unkn"):
            lbl.remove_css_class(c)
        if m == "NEW":
            lbl.add_css_class("new")
        elif m == "TRUST":
            lbl.add_css_class("trust")
        elif m == "UNKN":
            lbl.add_css_class("unkn")

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    col = Gtk.ColumnViewColumn.new("", factory)
    col.set_fixed_width(58)
    return col


def _flag_column() -> Gtk.ColumnViewColumn:
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        box = Gtk.Box(halign=Gtk.Align.START)
        lbl = Gtk.Label()
        lbl.add_css_class("ns-cell-flag")
        box.append(lbl)
        item.set_child(box)

    def bind(_f, item):
        obj = item.get_item()
        lbl = item.get_child().get_first_child()
        flag = obj.flag
        lbl.set_text(flag)
        lbl.set_visible(bool(flag))
        for c in ("gw", "arp"):
            lbl.remove_css_class(c)
        if flag == "GW":
            lbl.add_css_class("gw")
        elif flag == "ARP":
            lbl.add_css_class("arp")

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    col = Gtk.ColumnViewColumn.new("", factory)
    col.set_fixed_width(64)
    return col


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #

class NetScopeWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title=core.APP_NAME)
        self.set_default_size(1180, 760)
        self.set_decorated(False)
        self.add_css_class("ns-root")

        self._scan_stop = threading.Event()
        self._probe_stop = threading.Event()
        self._scanning = False
        self._probing = False
        self._host_index: dict[str, HostRow] = {}
        self._networks: list[tuple[str, str]] = []  # (network, iface)
        self._target_ip = ""
        self._cursor_on = True
        self._pulse = True
        self._probe_cache: dict[str, list] = {}   # ip -> [core.PortHit] from the last probe
        self._ai_windows: list = []
        self._ai_backend = ai.available_backend()
        self._public: dict = {}
        self._devices: dict = {}
        self._new_ips: set = set()
        self._watch_on = False
        self._watch_source = 0
        self._watch_interval = 120
        self._loading_identity = False

        self.host_store = Gio.ListStore.new(HostRow)
        self.port_store = Gio.ListStore.new(PortRow)

        overlay = Gtk.Overlay()
        self.set_child(overlay)
        overlay.set_child(self._build_layout())

        scan = Gtk.Box(can_target=False)
        scan.add_css_class("ns-scanlines")
        overlay.add_overlay(scan)
        vig = Gtk.Box(can_target=False)
        vig.add_css_class("ns-vignette")
        overlay.add_overlay(vig)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        GLib.timeout_add(530, self._blink)
        GLib.timeout_add(15000, self._refresh_interfaces_tick)

        self.log(f"{core.APP_NAME} v{core.VERSION} // {core.TAGLINE}")
        self.log(f"theme {theme.theme_name()} // font {theme.font_family()}")
        self.refresh_interfaces()
        self.refresh_public()
        self.refresh_wifi()
        self._update_inventory_badge()
        GLib.timeout_add(400, self._autoscan)

    # ---- layout ----------------------------------------------------------- #

    def _build_layout(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # header
        header = Gtk.Box(spacing=8)
        header.add_css_class("ns-header")
        brand = Gtk.Label(label="◢ NETSCOPE")
        brand.add_css_class("ns-brand")
        from gi.repository import Pango as _Pango
        sub = Gtk.Label(label="// LAN RECONNAISSANCE")
        sub.add_css_class("ns-brand-sub")
        sub.set_valign(Gtk.Align.END)
        sub.set_ellipsize(_Pango.EllipsizeMode.END)
        header.append(brand)
        header.append(sub)
        self.status_label = _label("ns-dim", xalign=1.0)
        self.status_label.set_hexpand(True)
        self.status_label.set_max_width_chars(20)
        header.append(self.status_label)
        self.watch_btn = Gtk.ToggleButton(label="WATCH")
        self.watch_btn.add_css_class("ns-watch")
        self.watch_btn.set_tooltip_text(
            "Keep sweeping and alert when an unknown device joins or a new port opens")
        self.watch_btn.connect("toggled", lambda b: self._toggle_watch(b.get_active()))
        header.append(self.watch_btn)
        self.live_label = Gtk.Label(label="● LIVE")
        self.live_label.add_css_class("ns-live")
        header.append(self.live_label)
        root.append(header)

        # body: draggable split between the interfaces sidebar and the hosts
        # area, so the ratio is the user's to set (drag the divider)
        body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        body.set_start_child(self._build_sidebar())
        body.set_end_child(self._build_main())
        body.set_position(206)
        body.set_resize_start_child(False)   # on window resize, the hosts side grows
        body.set_resize_end_child(True)
        body.set_shrink_start_child(False)   # don't let either side clip its content
        body.set_shrink_end_child(True)
        self.body_paned = body
        root.append(body)

        # log
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.log_view.add_css_class("ns-log")
        self.log_view.set_wrap_mode(Gtk.WrapMode.NONE)
        log_scroll = Gtk.ScrolledWindow(vexpand=False, min_content_height=88, max_content_height=88)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_child(self.log_view)
        root.append(log_scroll)

        # footer
        footer = Gtk.Box(spacing=8)
        footer.add_css_class("ns-footer")
        hint = _label("ns-dimmer ns-mono-small")
        hint.set_text("F5 scan   ENTER/ctrl+P probe   ctrl+I ai scan   ESC stop   ctrl+C copy ip   ctrl+Q quit")
        hint.set_hexpand(True)
        footer.append(hint)
        tag = Gtk.Label(label=core.TAGLINE.upper())
        tag.add_css_class("ns-tagline")
        footer.append(tag)
        self.cursor_label = Gtk.Label(label="█")
        self.cursor_label.add_css_class("ns-cursor")
        footer.append(self.cursor_label)
        root.append(footer)
        return root

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.add_css_class("ns-sidebar")
        side.set_size_request(150, -1)  # min width; drag the divider to size it

        sec = Gtk.Label(label="INTERFACES", xalign=0)
        sec.add_css_class("ns-section")
        side.append(sec)

        self.iface_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        iface_scroll = Gtk.ScrolledWindow(vexpand=True)
        iface_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        iface_scroll.set_child(self.iface_box)
        side.append(iface_scroll)

        self.wifi_sec = Gtk.Label(label="WI-FI", xalign=0)
        self.wifi_sec.add_css_class("ns-section")
        self.wifi_sec.set_visible(False)
        side.append(self.wifi_sec)
        self.wifi_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.wifi_card.add_css_class("ns-card")
        self.wifi_card.add_css_class("primary")
        self.wifi_card.set_margin_bottom(6)
        self.wifi_card.set_visible(False)
        self.wifi_ssid = _label("ns-card-title")
        self.wifi_bar = _label("ns-ip")
        self.wifi_meta = _label("ns-dim ns-mono-small")
        self.wifi_rate = _label("ns-dimmer ns-mono-small")
        for w in (self.wifi_ssid, self.wifi_bar, self.wifi_meta, self.wifi_rate):
            self.wifi_card.append(w)
        side.append(self.wifi_card)

        pub_head = Gtk.Box()
        sec2 = Gtk.Label(label="PUBLIC", xalign=0)
        sec2.add_css_class("ns-section")
        sec2.set_hexpand(True)
        pub_head.append(sec2)
        refresh = Gtk.Button(label="↻")
        refresh.add_css_class("ns-ghost")
        refresh.set_tooltip_text("Refresh public address")
        refresh.set_margin_end(10)
        refresh.set_margin_top(6)
        refresh.connect("clicked", lambda *_: self.refresh_public(force=True))
        pub_head.append(refresh)
        side.append(pub_head)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.add_css_class("ns-card")
        card.add_css_class("primary")
        card.set_margin_bottom(10)
        self.pub_ip4 = _label("ns-big-ip")
        self.pub_ip4.set_text("…")
        _copyable(self.pub_ip4, self)
        self.pub_ip6 = _label("ns-dim ns-mono-small")
        _copyable(self.pub_ip6, self)
        self.pub_org = _label("")
        self.pub_geo = _label("ns-dim")
        self.pub_host = _label("ns-dimmer ns-mono-small")
        for w in (self.pub_ip4, self.pub_ip6, self.pub_org, self.pub_geo, self.pub_host):
            card.append(w)
        side.append(card)
        return side

    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_vexpand(True)
        paned.set_position(440)
        paned.set_start_child(self._build_hosts())
        paned.set_end_child(self._build_probe())
        paned.set_resize_start_child(True)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        main.append(paned)
        return main

    def _build_hosts(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.add_css_class("ns-toolbar")
        top = Gtk.Box(spacing=10)
        t = Gtk.Label(label="HOSTS")
        t.add_css_class("ns-toolbar-title")
        top.append(t)
        self.inv_badge = Gtk.Button(label="")
        self.inv_badge.add_css_class("ns-inv-badge")
        self.inv_badge.set_tooltip_text("Click for an AI summary of the whole network")
        self.inv_badge.connect("clicked", lambda *_: self.start_ai_network())
        top.append(self.inv_badge)
        self.host_count = _label("ns-dim", xalign=1.0)
        self.host_count.set_hexpand(True)
        self.host_count.set_width_chars(8)
        self.host_count.set_text("no scan yet")
        top.append(self.host_count)
        wrap.append(top)
        bar = Gtk.Box(spacing=10)
        self.net_dropdown = _dropdown([""], 150)
        self.net_dropdown.set_tooltip_text("Network to sweep")
        bar.append(self.net_dropdown)
        self.net_entry = Gtk.Entry(placeholder_text="custom cidr")
        self.net_entry.set_hexpand(True)
        self.net_entry.set_size_request(80, -1)
        self.net_entry.connect("activate", lambda *_: self.start_scan())
        bar.append(self.net_entry)
        self.scan_btn = Gtk.Button(label="SCAN")
        self.scan_btn.connect("clicked", lambda *_: self.toggle_scan())
        bar.append(self.scan_btn)
        wrap.append(bar)
        box.append(wrap)

        self.scan_progress = Gtk.ProgressBar()
        box.append(self.scan_progress)

        self.host_sel = Gtk.SingleSelection.new(self.host_store)
        self.host_sel.set_autoselect(False)
        self.host_sel.connect("notify::selected-item", self._on_host_selected)
        view = Gtk.ColumnView.new(self.host_sel)
        view.set_show_row_separators(False)
        view.set_show_column_separators(False)
        view.connect("activate", lambda *_: self.start_probe())
        view.append_column(_text_column("IP", "ip", "ns-cell-ip", fixed=150))
        view.append_column(_flag_column())
        view.append_column(_mark_column())
        view.append_column(_text_column("NAME", "name", "", expand=True))
        view.append_column(_text_column("MAC", "mac", "ns-dim", fixed=170))
        view.append_column(_text_column("VENDOR", "vendor", "", expand=True))
        view.append_column(_text_column("RTT", "rtt", "ns-dim",
                                        fmt=lambda v: "" if v < 0 else f"{v:.1f} ms", fixed=95))
        view.append_column(_text_column("SEEN", "seen", "ns-dimmer", fixed=95))
        self.host_view = view
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(view)
        box.append(scroll)
        return box

    def _build_probe(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.add_css_class("ns-toolbar")
        top = Gtk.Box(spacing=10)
        t = Gtk.Label(label="PROBE")
        t.add_css_class("ns-toolbar-title")
        top.append(t)
        # fixed 15-character slot (an IPv4 never exceeds that) so a longer or
        # shorter address never changes the window's minimum width
        self.target_label = Gtk.Label(label="select a host", xalign=0)
        self.target_label.add_css_class("ns-target")
        self.target_label.set_width_chars(13)
        self.target_label.set_max_width_chars(15)
        from gi.repository import Pango
        self.target_label.set_ellipsize(Pango.EllipsizeMode.END)
        top.append(self.target_label)
        self.name_entry = Gtk.Entry(placeholder_text="name this device")
        self.name_entry.set_hexpand(True)
        self.name_entry.set_size_request(100, -1)
        self.name_entry.set_sensitive(False)
        self.name_entry.set_tooltip_text("Give this device a name (saved to your inventory)")
        self.name_entry.connect("activate", lambda *_: self._save_identity())
        fl = Gtk.EventControllerFocus()
        fl.connect("leave", lambda *_: self._save_identity())
        self.name_entry.add_controller(fl)
        top.append(self.name_entry)
        self.trust_btn = Gtk.ToggleButton(label="TRUST")
        self.trust_btn.add_css_class("ns-ghost")
        self.trust_btn.set_sensitive(False)
        self.trust_btn.set_tooltip_text("Mark this device as trusted/known")
        self.trust_btn.connect("toggled", lambda *_: self._save_identity())
        top.append(self.trust_btn)
        self.actions_btn = Gtk.MenuButton(label="⋯")
        self.actions_btn.add_css_class("ns-ghost")
        self.actions_btn.set_sensitive(False)
        self.actions_btn.set_tooltip_text("Actions for this device")
        self.actions_btn.set_popover(self._build_actions_popover())
        top.append(self.actions_btn)
        wrap.append(top)
        bar = Gtk.Box(spacing=10)
        wrap.append(bar)

        self.profile_dropdown = _dropdown(
            [core.PROFILES["quick"][0], core.PROFILES["common"][0], core.PROFILES["full"][0], "Custom"], 110
        )
        self.profile_dropdown.set_tooltip_text("Port profile")
        self.profile_dropdown.connect("notify::selected", self._on_profile_changed)
        bar.append(self.profile_dropdown)
        self.ports_entry = Gtk.Entry(placeholder_text="22,80,8000-8100")
        self.ports_entry.set_size_request(110, -1)
        self.ports_entry.set_hexpand(True)
        self.ports_entry.set_visible(False)
        self.ports_entry.connect("activate", lambda *_: self.start_probe())
        bar.append(self.ports_entry)
        self.banner_check = Gtk.CheckButton(label="banners")
        self.banner_check.set_active(True)
        self.banner_check.set_tooltip_text("Grab service banners from open ports")
        bar.append(self.banner_check)

        self.probe_btn = Gtk.Button(label="PROBE")
        self.probe_btn.set_sensitive(False)
        self.probe_btn.connect("clicked", lambda *_: self.toggle_probe())
        bar.append(self.probe_btn)

        self.ai_btn = Gtk.Button(label="◢ AI SCAN")
        self.ai_btn.set_sensitive(False)
        self.ai_btn.add_css_class("ns-ai")
        self.ai_btn.set_tooltip_text(
            "AI investigation: identify the device, its role and what each open port is for"
        )
        self.ai_btn.connect("clicked", lambda *_: self.start_ai_scan())
        bar.append(self.ai_btn)
        self.port_count = _label("ns-dim", xalign=1.0)
        self.port_count.set_hexpand(True)
        bar.append(self.port_count)
        box.append(wrap)

        self.probe_progress = Gtk.ProgressBar()
        box.append(self.probe_progress)

        self.sec_strip = Gtk.Box(spacing=10)
        self.sec_strip.add_css_class("ns-sec-strip")
        self.sec_strip.set_visible(False)
        self.risk_badge = Gtk.Label(label="")
        self.risk_badge.add_css_class("ns-risk-badge")
        self.sec_strip.append(self.risk_badge)
        self.sec_summary = _label("ns-dim")
        self.sec_summary.set_hexpand(True)
        self.sec_strip.append(self.sec_summary)
        self.findings_btn = Gtk.MenuButton(label="FINDINGS")
        self.findings_btn.add_css_class("ns-ghost")
        self.findings_pop = Gtk.Popover()
        self.findings_pop.add_css_class("ns-findings-pop")
        self.findings_btn.set_popover(self.findings_pop)
        self.sec_strip.append(self.findings_btn)
        box.append(self.sec_strip)

        sel = Gtk.NoSelection.new(self.port_store)
        view = Gtk.ColumnView.new(sel)
        self.port_view = view
        view.append_column(_text_column("PORT", "port", "ns-cell-ip", fixed=100))
        view.append_column(_text_column("SERVICE", "service", "", fixed=190))
        view.append_column(_text_column("LATENCY", "latency", "ns-dim",
                                        fmt=lambda v: f"{v:.1f} ms", fixed=110))
        view.append_column(_text_column("BANNER", "banner", "ns-dim", expand=True))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(view)
        box.append(scroll)
        return box

    # ---- helpers ----------------------------------------------------------- #

    def log(self, msg: str) -> None:
        buf = self.log_view.get_buffer()
        stamp = time.strftime("%H:%M:%S")
        buf.insert(buf.get_end_iter(), f"[{stamp}] > {msg}\n")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_mark_onscreen(mark)
        # keep the log bounded
        if buf.get_line_count() > 400:
            start = buf.get_start_iter()
            cut = buf.get_iter_at_line(100)[1]
            buf.delete(start, cut)

    def set_status(self, msg: str, busy: bool = False) -> None:
        self.status_label.set_text(msg)
        if busy:
            self.live_label.add_css_class("ns-busy")
        else:
            self.live_label.remove_css_class("ns-busy")

    def _blink(self) -> bool:
        self._cursor_on = not self._cursor_on
        if self._cursor_on:
            self.cursor_label.remove_css_class("off")
        else:
            self.cursor_label.add_css_class("off")
        busy = self._scanning or self._probing
        self.live_label.set_text("● BUSY" if busy else "● LIVE")
        if busy and not self._cursor_on:
            self.live_label.add_css_class("dim")
        else:
            self.live_label.remove_css_class("dim")
        return True

    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_F5:
            self.start_scan()
            return True
        if keyval == Gdk.KEY_Escape:
            self.stop_all()
            return True
        if ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self.start_probe()
            return True
        if ctrl and keyval in (Gdk.KEY_i, Gdk.KEY_I):
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            if shift:
                self.start_ai_network()
            else:
                self.start_ai_scan()
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_target()
            return True
        if ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.close()
            return True
        if ctrl and keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.refresh_public(force=True)
            self.refresh_interfaces()
            return True
        if ctrl and keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self._export_report()
            return True
        return False

    def copy_target(self) -> None:
        if self._target_ip:
            self.copy_text(self._target_ip)

    def copy_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text or text in ("…", "OFFLINE", "no ipv6"):
            return
        Gdk.Display.get_default().get_clipboard().set(text)
        self.log(f"copied {text} to clipboard")

    # ---- interfaces -------------------------------------------------------- #

    def _refresh_interfaces_tick(self) -> bool:
        if not (self._scanning or self._probing):
            self.refresh_interfaces(quiet=True)
            self.refresh_wifi()
        return True

    def refresh_wifi(self) -> None:
        def work():
            info = core.wifi_status()
            GLib.idle_add(self._apply_wifi, info)
        threading.Thread(target=work, daemon=True).start()

    def _apply_wifi(self, info) -> bool:
        show = bool(info)
        self.wifi_sec.set_visible(show)
        self.wifi_card.set_visible(show)
        if not show:
            return False
        sec = info.get("security") or "open"
        self.wifi_ssid.set_text(f"󰖩  {info.get('ssid','?')}")
        pct = info.get("signal_pct", -1)
        dbm = info.get("signal_dbm", 0)
        blocks = 0 if pct < 0 else max(1, round(pct / 20))
        bar = "▊" * blocks + "·" * (5 - blocks)
        self.wifi_bar.set_text(f"{bar}  {pct if pct>=0 else '?'}%" + (f"  {dbm} dBm" if dbm else ""))
        band = info.get("band", "")
        chan = info.get("channel", "")
        self.wifi_meta.set_text("  ·  ".join(x for x in (band, f"ch {chan}" if chan else "", sec) if x))
        self.wifi_rate.set_text(info.get("rate", ""))
        return False

    def refresh_interfaces(self, quiet: bool = False) -> None:
        ifaces = core.interfaces()
        child = self.iface_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.iface_box.remove(child)
            child = nxt

        nets: list[tuple[str, str]] = []
        for i in ifaces:
            if i.kind == "loopback":
                continue
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            card.add_css_class("ns-card")
            if not i.up:
                card.add_css_class("down")
            if i.is_default:
                card.add_css_class("primary")

            head = Gtk.Box(spacing=8)
            name = Gtk.Label(label=f"{KIND_GLYPH.get(i.kind, '󰛳')}  {i.name}", xalign=0)
            name.add_css_class("ns-card-title")
            name.set_hexpand(True)
            head.append(name)
            state = Gtk.Label(label=("DEFAULT" if i.is_default else i.state.upper()))
            state.add_css_class("ns-good" if i.up else "ns-dimmer")
            state.add_css_class("ns-card-kind")
            head.append(state)
            card.append(head)

            if i.ipv4:
                for a in i.ipv4:
                    l = _label("ns-ip")
                    l.set_text(a.cidr)
                    _copyable(l, self)
                    card.append(l)
            else:
                l = _label("ns-dimmer")
                l.set_text("no ipv4")
                card.append(l)
            for a in i.ipv6[:2]:
                l = _label("ns-dim ns-mono-small")
                l.set_text(a.addr)
                _copyable(l, self)
                card.append(l)
            meta = _label("ns-dimmer ns-mono-small")
            bits = [i.kind]
            if i.mac:
                bits.append(i.mac)
            if i.gateway:
                bits.append(f"gw {i.gateway}")
            meta.set_text("  ·  ".join(bits))
            card.append(meta)
            self.iface_box.append(card)
            if i.up:
                for n in i.networks:
                    nets.append((n, i.name))

        if nets != self._networks:
            self._networks = nets
            labels = [f"{n} · {dev}" if dev else n for n, dev in nets] or ["no network"]
            self.net_dropdown.set_model(Gtk.StringList.new(labels))
            self.net_dropdown.set_selected(0)
        if not quiet:
            up = [i.name for i in ifaces if i.up and i.kind != "loopback"]
            self.log(f"interfaces: {', '.join(up) if up else 'none up'}")

    # ---- public ------------------------------------------------------------ #

    def refresh_public(self, force: bool = False) -> None:
        self.pub_ip4.set_text("…")
        if force:
            self.log("resolving public address")

        def work():
            info = core.public_info(force=force)
            GLib.idle_add(self._apply_public, info)

        threading.Thread(target=work, daemon=True).start()

    def _apply_public(self, info: core.PublicInfo) -> bool:
        self._public = {"ipv4": info.ipv4, "org": info.org, "asn": info.asn,
                        "city": info.city, "region": info.region, "country": info.country}
        if info.error and not info.ipv4:
            self.pub_ip4.set_text("OFFLINE")
            self.pub_org.set_text(info.error)
            self.pub_ip6.set_text("")
            self.pub_geo.set_text("")
            self.pub_host.set_text("")
            self.log("public address: " + info.error)
            return False
        self.pub_ip4.set_text(info.ipv4)
        self.pub_ip6.set_text(info.ipv6 or "no ipv6")
        org = info.org
        if info.asn:
            org = f"{org}  ·  {info.asn}" if org else info.asn
        self.pub_org.set_text(org)
        self.pub_geo.set_text(", ".join(x for x in (info.city, info.region, info.country) if x))
        self.pub_host.set_text(info.hostname)
        self.log(f"public {info.ipv4}" + (f" via {info.org}" if info.org else ""))
        return False

    # ---- scanning ---------------------------------------------------------- #

    def _autoscan(self) -> bool:
        if self._networks:
            self.start_scan()
        else:
            self.log("no active network to sweep")
        return False

    def _selected_network(self) -> tuple[str, str]:
        custom = self.net_entry.get_text().strip()
        if custom:
            try:
                net = ipaddress.ip_network(custom, strict=False)
                return str(net), ""
            except ValueError:
                self.log(f"bad cidr: {custom}")
                return "", ""
        idx = self.net_dropdown.get_selected()
        if 0 <= idx < len(self._networks):
            return self._networks[idx]
        return "", ""

    def toggle_scan(self) -> None:
        if self._scanning:
            self._scan_stop.set()
            self.log("scan aborted")
        else:
            self.start_scan()

    def stop_all(self) -> None:
        if self._scanning:
            self._scan_stop.set()
            self.log("scan aborted")
        if self._probing:
            self._probe_stop.set()
            self.log("probe aborted")

    def start_scan(self) -> None:
        if self._scanning:
            return
        network, iface = self._selected_network()
        if not network:
            return
        self._scanning = True
        self._scan_stop = threading.Event()
        self.scan_btn.set_label("STOP")
        self.scan_btn.add_css_class("ns-danger")
        self.host_store.remove_all()
        self._host_index.clear()
        self.scan_progress.set_fraction(0)
        self.host_count.set_text("0 hosts")
        self.set_status(f"sweeping {network}", busy=True)
        self.log(f"sweep {network}" + (f" on {iface}" if iface else ""))
        self._devices = store.load()
        stop = self._scan_stop

        def on_host(h: core.Host):
            GLib.idle_add(self._add_live_host, h)

        def progress(done, total, msg):
            GLib.idle_add(self._scan_progress, done, total, msg)

        def work():
            try:
                result = core.sweep(network, iface, progress=progress, stop=stop, on_host=on_host)
                GLib.idle_add(self._scan_done, result)
            except Exception as e:  # surface to the log instead of dying silently
                GLib.idle_add(self._scan_failed, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _enrich_row(self, row: "HostRow") -> None:
        """Overlay a row with what the inventory remembers: a user name, trust,
        and a NEW / UNKN / TRUST mark."""
        d = self._devices.get(row.key)
        if d is not None:
            if d.name:
                row.name = d.name
            trusted = d.trusted
        else:
            trusted = row.flag == "SELF"
        if row.ip in self._new_ips:
            row.mark = "NEW"
        elif trusted or row.flag in ("SELF",):
            row.mark = "TRUST"
        elif row.flag == "GW":
            row.mark = ""
        else:
            row.mark = "UNKN"

    def _add_live_host(self, h: core.Host) -> bool:
        if h.ip in self._host_index:
            return False
        row = HostRow.from_host(h)
        self._enrich_row(row)
        self._host_index[h.ip] = row
        # insert sorted by ip
        n = self.host_store.get_n_items()
        pos = n
        for i in range(n):
            if self.host_store.get_item(i).ipnum > row.ipnum:
                pos = i
                break
        self.host_store.insert(pos, row)
        if pos == 0:
            self.host_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        self.host_count.set_text(f"{self.host_store.get_n_items()} hosts")
        return False

    def _scan_progress(self, done: int, total: int, msg: str) -> bool:
        if total:
            self.scan_progress.set_fraction(min(1.0, done / total))
        if msg.startswith("echo"):
            self.log(msg)
        elif msg == "resolving names":
            self.set_status("resolving names", busy=True)
            self.log("resolving names + vendors")
        return False

    def _scan_done(self, result: core.ScanResult) -> bool:
        self._scanning = False
        self.scan_btn.set_label("SCAN")
        self.scan_btn.remove_css_class("ns-danger")
        self.scan_progress.set_fraction(1.0)

        # record into the persistent inventory and surface what changed
        events = store.record_sweep(result.hosts)
        self._new_ips = {e["ip"] for e in events if e["type"] == store.EV_NEW}
        self._devices = store.load()
        if self._watch_on and events:
            notify.notify_events(events)
        for e in events:
            self._log_event(e)

        self.host_store.remove_all()
        self._host_index.clear()
        for h in result.hosts:
            row = HostRow.from_host(h)
            self._enrich_row(row)
            self._host_index[h.ip] = row
            self.host_store.append(row)
        n = len(result.hosts)
        self.host_count.set_text(f"{n} hosts  ·  {result.total_probed} probed  ·  {result.duration:.1f}s")
        self.set_status(f"{n} hosts on {result.network}")
        named = sum(1 for h in result.hosts if h.hostname or h.mdns)
        self.log(f"sweep complete: {n} hosts, {named} named, {result.duration:.1f}s")
        self._update_inventory_badge()
        if self._target_ip and self._target_ip in self._host_index:
            self._select_ip(self._target_ip)
        return False

    def _log_event(self, e: dict) -> None:
        label = {
            store.EV_NEW: "NEW DEVICE", store.EV_GONE: "device left",
            store.EV_BACK: "device back", store.EV_PORT_NEW: "NEW PORT",
        }.get(e["type"], e["type"])
        who = e.get("name") or e.get("ip")
        extra = f" {e['detail']}" if e.get("detail") else ""
        self.log(f"! {label}: {who} ({e.get('ip','')}){extra}")

    def _update_inventory_badge(self) -> None:
        summ = store.summary()
        u = summ.get("unknown", 0)
        self.inv_badge.set_label(f"⚠ {u}" if u else "✓")
        self.inv_badge.set_tooltip_text(
            (f"{u} device(s) present that you have not marked trusted" if u
             else "every present device is trusted") + " — click WATCH to monitor")
        self.inv_badge.remove_css_class("alert")
        self.inv_badge.remove_css_class("ok")
        self.inv_badge.add_css_class("alert" if u else "ok")

    def _scan_failed(self, err: str) -> bool:
        self._scanning = False
        self.scan_btn.set_label("SCAN")
        self.scan_btn.remove_css_class("ns-danger")
        self.set_status("scan failed")
        self.log(f"scan failed: {err}")
        return False

    def _select_ip(self, ip: str) -> None:
        for i in range(self.host_store.get_n_items()):
            if self.host_store.get_item(i).ip == ip:
                self.host_sel.set_selected(i)
                return

    # ---- probing ----------------------------------------------------------- #

    def _on_host_selected(self, *_):
        item = self.host_sel.get_selected_item()
        if not item:
            return
        self._target_ip = item.ip
        self.target_label.set_text(item.ip)
        self.probe_btn.set_sensitive(True)
        d = self._devices.get(item.key)
        self._loading_identity = True
        self.name_entry.set_sensitive(True)
        self.name_entry.set_text(d.name if d else "")
        self.trust_btn.set_sensitive(True)
        self.trust_btn.set_active(d.trusted if d else (item.flag == "SELF"))
        self.actions_btn.set_sensitive(True)
        self._loading_identity = False
        self.ai_btn.set_sensitive(bool(self._ai_backend))
        if not self._ai_backend:
            self.ai_btn.set_tooltip_text("No AI CLI found (install claude, gemini or codex)")
        if not self._probing:
            self.port_store.remove_all()
            self.port_count.set_text("")
            self.probe_progress.set_fraction(0)
            self.sec_strip.set_visible(False)

    def _build_actions_popover(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.add_css_class("ns-findings-pop")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(8); box.set_margin_end(8)
        box.set_size_request(180, -1)
        for label, handler in (
            ("󰖟  Open web UI", self._act_web),
            ("󰣀  SSH", self._act_ssh),
            ("󰤄  Wake-on-LAN", self._act_wake),
            ("󰐷  Ping", self._act_ping),
            ("󰆏  Copy IP", lambda: self.copy_target()),
        ):
            b = Gtk.Button(label=label)
            b.add_css_class("ns-ghost")
            b.set_halign(Gtk.Align.FILL)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _w, h=handler: (self.actions_btn.popdown(), h()))
            box.append(b)
        pop.set_child(box)
        return pop

    def _sel_host(self):
        return self.host_sel.get_selected_item()

    def _act_web(self) -> None:
        h = self._sel_host()
        if not h:
            return
        ports = self._probe_cache.get(h.ip)
        pdict = {x.port: x.service for x in ports} if ports else None
        url = actions.web_url(h.ip, pdict)
        actions.open_url(url)
        self.log(f"opening {url}")

    def _act_ssh(self) -> None:
        h = self._sel_host()
        if h:
            self.log(f"ssh {h.ip}" if actions.ssh(h.ip) else "no terminal found for ssh")

    def _act_ping(self) -> None:
        h = self._sel_host()
        if h:
            self.log(f"ping {h.ip}" if actions.ping(h.ip) else "no terminal found for ping")

    def _act_wake(self) -> None:
        h = self._sel_host()
        if not h:
            return
        if not h.mac:
            self.log("no MAC to wake")
            return
        ok = actions.wake(h.mac)
        self.log(f"wake-on-lan {'sent to' if ok else 'failed for'} {h.mac}")

    def _export_report(self) -> None:
        import time as _t
        path = str(Path.home() / f"netscope-report-{_t.strftime('%Y%m%d-%H%M%S')}.html")
        try:
            with open(path, "w") as f:
                f.write(report.build("html"))
            self.log(f"report written: {path}")
            actions.open_url(path)
        except OSError as e:
            self.log(f"report failed: {e}")

    def _save_identity(self) -> None:
        if getattr(self, "_loading_identity", False):
            return
        item = self.host_sel.get_selected_item()
        if not item:
            return
        name = self.name_entry.get_text().strip()
        trusted = self.trust_btn.get_active()
        d = self._devices.get(item.key)
        if d is not None and d.name == name and d.trusted == trusted:
            return
        store.update_device(item.key, name=name, trusted=trusted)
        self._devices = store.load()
        item.name = name or item.mdns or item.vendor or item.ip
        self._enrich_row(item)
        self._update_inventory_badge()
        self.log(f"inventory: {item.ip} named '{name or '(cleared)'}', "
                 f"{'trusted' if trusted else 'untrusted'}")

    def _toggle_watch(self, on: bool) -> None:
        if on and not self._watch_on:
            self._watch_on = True
            self._watch_source = GLib.timeout_add_seconds(self._watch_interval, self._watch_tick)
            self.log(f"watch on — sweeping every {self._watch_interval}s, notifying on changes")
        elif not on and self._watch_on:
            self._watch_on = False
            if self._watch_source:
                GLib.source_remove(self._watch_source)
                self._watch_source = 0
            self.log("watch off")

    def _watch_tick(self) -> bool:
        if not self._watch_on:
            return False
        if not self._scanning and not self._probing:
            self.start_scan()
        return True

    def _on_profile_changed(self, *_):
        self.ports_entry.set_visible(self.profile_dropdown.get_selected() == 3)

    def _selected_ports(self) -> tuple[list[int], str]:
        idx = self.profile_dropdown.get_selected()
        key = ("quick", "common", "full", "custom")[idx]
        if key == "custom":
            spec = self.ports_entry.get_text().strip()
            return core.parse_ports(spec), spec or "custom"
        return core.profile_ports(key), core.PROFILES[key][0].lower()

    def toggle_probe(self) -> None:
        if self._probing:
            self._probe_stop.set()
            self.log("probe aborted")
        else:
            self.start_probe()

    def start_probe(self) -> None:
        if self._probing or not self._target_ip:
            return
        ports, label = self._selected_ports()
        if not ports:
            self.log("no ports to probe")
            return
        ip = self._target_ip
        self._probing = True
        self._probe_stop = threading.Event()
        stop = self._probe_stop
        grab = self.banner_check.get_active()
        self.probe_btn.set_label("STOP")
        self.probe_btn.add_css_class("ns-danger")
        self.port_store.remove_all()
        self.probe_progress.set_fraction(0)
        self.port_count.set_text(f"0 open  ·  0/{len(ports)}")
        self.set_status(f"probing {ip} ({label}, {len(ports)} ports)", busy=True)
        self.log(f"probe {ip} :: {label} profile, {len(ports)} tcp ports")

        def on_hit(h: core.PortHit):
            GLib.idle_add(self._add_hit, h)

        def progress(done, total, msg):
            GLib.idle_add(self._probe_progress, done, total)

        def work():
            t0 = time.time()
            try:
                hits = core.probe(ip, ports, progress=progress, grab_banners=grab, stop=stop, on_hit=on_hit)
                GLib.idle_add(self._probe_done, ip, hits, len(ports), time.time() - t0, stop.is_set())
            except Exception as e:
                GLib.idle_add(self._probe_failed, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _add_hit(self, h: core.PortHit) -> bool:
        row = PortRow.from_hit(h)
        n = self.port_store.get_n_items()
        pos = n
        for i in range(n):
            if self.port_store.get_item(i).port > h.port:
                pos = i
                break
        self.port_store.insert(pos, row)
        if pos == 0:
            self.port_view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        self.log(f"open {h.port}/tcp {h.service}" + (f"  [{h.banner}]" if h.banner else ""))
        return False

    def _probe_progress(self, done: int, total: int) -> bool:
        if total:
            self.probe_progress.set_fraction(min(1.0, done / total))
        self.port_count.set_text(f"{self.port_store.get_n_items()} open  ·  {done}/{total}")
        return False

    def _probe_done(self, ip: str, hits, total: int, secs: float, aborted: bool) -> bool:
        self._probing = False
        self.probe_btn.set_label("PROBE")
        self.probe_btn.remove_css_class("ns-danger")
        if not aborted:
            self.probe_progress.set_fraction(1.0)
            self._probe_cache[ip] = list(hits)
            if len(self._probe_cache) > 64:
                self._probe_cache.pop(next(iter(self._probe_cache)))
        self.port_count.set_text(f"{len(hits)} open  ·  {total} probed  ·  {secs:.1f}s")
        self.set_status(f"{ip}: {len(hits)} open ports" + (" (aborted)" if aborted else ""))
        self.log(f"probe complete: {len(hits)} open on {ip} in {secs:.1f}s")
        self._assess_host(ip, hits)
        return False

    def _assess_host(self, ip: str, hits) -> None:
        findings, score, lvl = assess.assess(hits)
        # persist the score on the device record
        row = self._host_index.get(ip)
        key = store.device_key(row.mac if row else "", ip)
        store.update_device(key, risk=score)
        self._devices = store.load()
        # badge + summary
        self.risk_badge.set_text(f"RISK {score}")
        for c in ("clean", "low", "elevated", "high"):
            self.risk_badge.remove_css_class(c)
        self.risk_badge.add_css_class(lvl)
        self.risk_badge.set_tooltip_text(f"security posture: {lvl} ({score}/100)")
        self.sec_summary.set_text(assess.summary_line(findings, score).split("·", 1)[-1].strip())
        self._fill_findings(findings)
        self.sec_strip.set_visible(True)
        real = [f for f in findings if f.severity != "info"]
        if real:
            top = real[0]
            self.log(f"posture {lvl} ({score}/100): {len(real)} finding(s), "
                     f"top — {top.title}")
        else:
            self.log(f"posture clean ({score}/100): nothing notable")

    def _fill_findings(self, findings) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_size_request(360, -1)
        for f in findings:
            row = Gtk.Box(spacing=8)
            sev = Gtk.Label(label=f.severity.upper())
            sev.add_css_class("ns-sev")
            sev.add_css_class(f.severity)
            sev.set_valign(Gtk.Align.START)
            sev.set_width_chars(7)
            sev.set_xalign(0)
            row.append(sev)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(label=f.title + (f"  ·  {f.port}/tcp" if f.port else ""), xalign=0)
            title.add_css_class("ns-card-title")
            title.set_wrap(True)
            det = _label("ns-dim")
            det.set_text(f.detail)
            det.set_wrap(True)
            det.set_xalign(0)
            det.set_max_width_chars(46)
            col.append(title)
            col.append(det)
            row.append(col)
            box.append(row)
        self.findings_pop.set_child(box)

    # ---- AI investigation -------------------------------------------------- #

    def _core_host(self, ip: str) -> "core.Host":
        row = self._host_index.get(ip)
        h = core.Host(ip=ip)
        if row is not None:
            h.mac = row.mac
            h.vendor = row.vendor
            h.hostname = row.name
            h.rtt_ms = row.rtt
            h.is_gateway = row.flag == "GW"
            h.is_self = row.flag == "SELF"
            h.seen_by = row.seen.split("+") if row.seen else []
        h.is_gateway = h.is_gateway or ip in set(core.default_routes().values())
        return h

    def start_ai_scan(self) -> None:
        if not self._target_ip:
            return
        if not self._ai_backend:
            self.log("no AI CLI available (install claude, gemini or codex)")
            return
        ip = self._target_ip
        host = self._core_host(ip)
        hits = self._probe_cache.get(ip)
        win = AiScanWindow(self, ip, host, hits, self._ai_backend)
        self._ai_windows.append(win)
        self.log(f"ai scan {ip} via {self._ai_backend}")
        win.present()
        win.start()

    def start_ai_network(self) -> None:
        if not self._ai_backend:
            self.log("no AI engine available (install claude/gemini/codex, or run ollama)")
            return
        devices = [asdict(d) for d in store.load().values()]
        if not devices:
            self.log("run a scan first — nothing in the inventory to summarize")
            return
        win = AiScanWindow(self, "network", backend=self._ai_backend, mode="network",
                           devices=devices, public=dict(self._public))
        self._ai_windows.append(win)
        self.log(f"ai network summary via {self._ai_backend} ({len(devices)} devices)")
        win.present()
        win.start()

    def _probe_failed(self, err: str) -> bool:
        self._probing = False
        self.probe_btn.set_label("PROBE")
        self.probe_btn.remove_css_class("ns-danger")
        self.set_status("probe failed")
        self.log(f"probe failed: {err}")
        return False


class AiScanWindow(Gtk.Window):
    """A themed window that fingerprints a host, hands the evidence to an AI
    CLI, and streams the investigation report back. Developed for and by frig.
    """

    def __init__(self, parent: "NetScopeWindow", ip: str, host=None, hits=None,
                 backend=None, mode: str = "device", devices=None, public=None):
        title = (f"{core.APP_NAME} · AI network summary" if mode == "network"
                 else f"{core.APP_NAME} · AI investigation · {ip}")
        super().__init__(title=title)
        # register with the app so this window carries the io.github.frigstah.netscope class
        # (not "python3"), which the Hyprland float rule matches on
        appref = parent.get_application()
        if appref is not None:
            appref.add_window(self)
        self.set_transient_for(parent)
        self.set_default_size(900, 680)
        self.set_size_request(360, 280)
        self.set_decorated(False)
        self.add_css_class("ns-root")
        self._parent = parent
        self.ip = ip
        self.host = host
        self.hits = hits
        self.backend = backend
        self.mode = mode
        self.devices = devices or []
        self.public = public or {}
        self._engines = ai.engines()
        self._stop = threading.Event()
        self._buffer_started = False
        self._cursor_on = True

        overlay = Gtk.Overlay()
        self.set_child(overlay)
        overlay.set_child(self._build())
        scan = Gtk.Box(can_target=False)
        scan.add_css_class("ns-scanlines")
        overlay.add_overlay(scan)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        GLib.timeout_add(530, self._blink)

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Gtk.Box(spacing=12)
        header.add_css_class("ns-header")
        brand = Gtk.Label(label="◢ AI NETWORK" if self.mode == "network" else "◢ AI INVESTIGATION")
        brand.add_css_class("ns-brand")
        header.append(brand)
        target = Gtk.Label(label="whole network" if self.mode == "network" else self.ip)
        target.add_css_class("ns-accent")
        target.set_valign(Gtk.Align.END)
        header.append(target)
        self.status = _label("ns-dim", xalign=1.0)
        self.status.set_hexpand(True)
        header.append(self.status)
        # engine picker (cloud CLIs and/or local Ollama)
        if self._engines:
            ids = [e[0] for e in self._engines]
            self._engine_ids = ids
            self.engine_dd = _dropdown([e[1] for e in self._engines], 150, cap=22)
            sel = ids.index(self.backend) if self.backend in ids else 0
            self.backend = ids[sel]
            self.engine_dd.set_selected(sel)
            self.engine_dd.set_tooltip_text("Which AI engine to use (cloud, or local Ollama)")
            self.engine_dd.connect("notify::selected",
                                   lambda dd, _p: setattr(self, "backend", self._engine_ids[dd.get_selected()]))
            header.append(self.engine_dd)
        self.spinner = Gtk.Label(label="●")
        self.spinner.add_css_class("ns-live")
        header.append(self.spinner)
        root.append(header)

        self.progress = Gtk.ProgressBar()
        self.progress.set_fraction(0)
        root.append(self.progress)

        self.report_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.report_view.add_css_class("ns-report")
        self.report_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.report_view.set_left_margin(14)
        self.report_view.set_right_margin(14)
        self.report_view.set_top_margin(10)
        self.report_view.set_bottom_margin(10)
        buf = self.report_view.get_buffer()
        buf.create_tag("head", weight=700)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.report_view)
        root.append(scroll)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("ns-footer")
        hint = _label("ns-dimmer ns-mono-small")
        hint.set_text("ESC stop/close   ctrl+C copy report")
        hint.set_hexpand(True)
        footer.append(hint)
        self.copy_btn = Gtk.Button(label="COPY")
        self.copy_btn.add_css_class("ns-ghost")
        self.copy_btn.connect("clicked", lambda *_: self._copy())
        footer.append(self.copy_btn)
        self.rerun_btn = Gtk.Button(label="RERUN")
        self.rerun_btn.add_css_class("ns-ghost")
        self.rerun_btn.set_tooltip_text("Run again with the selected engine")
        self.rerun_btn.connect("clicked", lambda *_: self.start())
        footer.append(self.rerun_btn)
        self.stop_btn = Gtk.Button(label="STOP")
        self.stop_btn.add_css_class("ns-danger")
        self.stop_btn.connect("clicked", lambda *_: self._stop.set())
        footer.append(self.stop_btn)
        tag = Gtk.Label(label=core.TAGLINE.upper())
        tag.add_css_class("ns-tagline")
        footer.append(tag)
        root.append(footer)
        return root

    def _blink(self) -> bool:
        if self._done_flag():
            self.spinner.set_text("●")
            self.spinner.remove_css_class("dim")
            return False
        self._cursor_on = not self._cursor_on
        if self._cursor_on:
            self.spinner.remove_css_class("dim")
        else:
            self.spinner.add_css_class("dim")
        return True

    def _done_flag(self) -> bool:
        return getattr(self, "_finished", False)

    def start(self) -> None:
        # reset so RERUN works
        self._finished = False
        self._stop = threading.Event()
        self.stop_btn.set_sensitive(True)
        self.report_view.get_buffer().set_text("")
        self.progress.set_fraction(0)
        GLib.timeout_add(530, self._blink)
        ip, host, hits, backend, stop = self.ip, self.host, self.hits, self.backend, self._stop

        def set_status(msg, frac=None):
            self.status.set_text(msg)
            if frac is not None:
                self.progress.set_fraction(frac)
            return False

        if self.mode == "network":
            def net_work():
                GLib.idle_add(set_status, f"asking {backend}…", 0.6)
                GLib.idle_add(self._begin_report)
                ai.investigate_network(
                    self.devices, self.public,
                    on_delta=lambda t: GLib.idle_add(self._append, t),
                    on_done=lambda full, err: GLib.idle_add(self._finish, err),
                    stop=stop, backend=backend,
                )
            threading.Thread(target=net_work, daemon=True).start()
            return

        def work():
            local_hits = hits
            if local_hits is None:
                GLib.idle_add(set_status, "probing ports…", 0.1)
                try:
                    local_hits = core.probe(ip, core.profile_ports("quick"), stop=stop)
                except Exception:
                    local_hits = []
                GLib.idle_add(self._parent._probe_cache.__setitem__, ip, list(local_hits))
            if stop.is_set():
                GLib.idle_add(self._finish, "stopped")
                return
            GLib.idle_add(set_status, "fingerprinting device…", 0.35)
            try:
                dossier = recon.fingerprint(
                    ip, host, local_hits,
                    progress=lambda m: GLib.idle_add(set_status, m, 0.5),
                )
            except Exception as e:
                GLib.idle_add(self._finish, f"fingerprint failed: {e}")
                return
            # persist the discovered device type on the inventory record
            try:
                key = store.device_key(host.mac if host else "", ip)
                if dossier.dtype:
                    store.update_device(key, dtype=dossier.dtype)
            except Exception:
                pass
            if stop.is_set():
                GLib.idle_add(self._finish, "stopped")
                return
            GLib.idle_add(set_status, f"asking {backend}…", 0.7)
            GLib.idle_add(self._begin_report)
            ai.investigate(
                dossier,
                on_delta=lambda t: GLib.idle_add(self._append, t),
                on_done=lambda full, err: GLib.idle_add(self._finish, err),
                stop=stop,
                backend=backend,
            )

        threading.Thread(target=work, daemon=True).start()

    def _begin_report(self) -> bool:
        self._buffer_started = True
        self.progress.set_fraction(0.85)
        self.status.set_text("receiving report…")
        return False

    def _append(self, text: str) -> bool:
        buf = self.report_view.get_buffer()
        end = buf.get_end_iter()
        # bold an all-caps header line
        for chunk in text.splitlines(keepends=True):
            stripped = chunk.strip()
            end = buf.get_end_iter()
            if stripped and stripped == stripped.upper() and len(stripped) <= 24 and stripped[0].isalpha():
                buf.insert_with_tags_by_name(end, chunk, "head")
            else:
                buf.insert(end, chunk)
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.report_view.scroll_mark_onscreen(mark)
        return False

    def _finish(self, err: str) -> bool:
        self._finished = True
        self.progress.set_fraction(1.0)
        self.stop_btn.set_sensitive(False)
        self.spinner.set_text("●")
        self.spinner.remove_css_class("dim")
        if err and err != "stopped":
            self.status.set_text("error")
            self.spinner.add_css_class("ns-bad-live")
            buf = self.report_view.get_buffer()
            buf.insert(buf.get_end_iter(), f"\n\n[investigation failed: {err}]\n")
        elif err == "stopped":
            self.status.set_text("stopped")
        else:
            self.status.set_text("investigation complete")
        return False

    def _copy(self) -> None:
        buf = self.report_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if text.strip():
            Gdk.Display.get_default().get_clipboard().set(text)
            self.status.set_text("report copied")

    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape:
            if self._done_flag():
                self.close()
            else:
                self._stop.set()
                self.status.set_text("stopping…")
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self._copy()
            return True
        return False

    def _on_close(self, *_):
        self._stop.set()
        if self in self._parent._ai_windows:
            self._parent._ai_windows.remove(self)
        return False


class NetScopeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=core.APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_string(theme.css())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )

    def do_activate(self):
        if not self.win:
            self.win = NetScopeWindow(self)
        self.win.present()


def main() -> int:
    import sys
    app = NetScopeApp()
    return app.run(sys.argv[:1])
