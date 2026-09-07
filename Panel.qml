import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// NetScope bar button + popout. Developed for and by frig.
//
// The popout is a read-only glance: interfaces with their addresses, the
// public address, and the last LAN sweep. Everything it knows comes from
// `netscope --status --json`; the heavy lifting lives in the NetScope window.
Panel {
  id: root
  moduleName: "io.github.frigstah.netscope"
  ipcTarget: "io.github.frigstah.netscope"

  readonly property string bin: Quickshell.env("HOME") + "/.config/omarchy/plugins/io.github.frigstah.netscope/bin/netscope"
  // Bar glyph: a few compact, network-flavoured options. OpticalGlyph centres
  // them so they sit at the same height as the neighbouring bar icons.
  readonly property var glyphMap: ({
    "access-point": "󰀃", "radar": "󰆤", "wifi": "󰤨", "lan": "󰛳", "crosshairs": ""
  })
  readonly property string barGlyphName: setting("barGlyph", "access-point")
  readonly property string barGlyph: glyphMap[barGlyphName] || glyphMap["access-point"]
  readonly property int pollSeconds: Math.max(5, setting("pollSeconds", 30))
  readonly property bool autoScanOnOpen: setting("autoScanOnOpen", false) === true

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color accent: fg
  readonly property color dim: Qt.darker(fg, 1.8)
  readonly property color deep: Qt.darker(fg, 3.2)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ---- state from `netscope --status --json` -------------------------------
  property var ifaces: []
  property var pub: ({})
  property var lastScan: ({})
  property var inventory: ({})
  readonly property int unknownCount: inventory && inventory.unknown ? inventory.unknown : 0
  property bool loaded: false
  property bool scanning: false
  property string lastError: ""

  property bool cursorActive: false
  property int cursorIndex: 0
  property string tick: ""

  readonly property var rows: ["open", "rescan", "refresh"]
  readonly property string currentRow: rows[Math.max(0, Math.min(cursorIndex, rows.length - 1))]

  readonly property string statusLine: {
    if (lastError !== "") return lastError
    if (scanning) return "SWEEPING // " + (lastScan.network || "LAN")
    if (!loaded) return "LOADING"
    var up = 0
    for (var i = 0; i < ifaces.length; i++) if (ifaces[i].up) up++
    var s = up + " LINK" + (up === 1 ? "" : "S") + " UP"
    if (lastScan.hosts > 0) s += " // " + lastScan.hosts + " HOSTS"
    if (unknownCount > 0) s += " // " + unknownCount + " UNKNOWN"
    return s
  }

  function ageText(at) {
    if (!at) return "never"
    var s = Math.max(0, Math.round(Date.now() / 1000 - at))
    if (s < 60) return s + "s ago"
    if (s < 3600) return Math.round(s / 60) + "m ago"
    if (s < 86400) return Math.round(s / 3600) + "h ago"
    return Math.round(s / 86400) + "d ago"
  }

  function kindGlyph(kind) {
    if (kind === "wifi") return "󰖩"
    if (kind === "ethernet") return "󰈀"
    if (kind === "vpn") return "󰖂"
    if (kind === "virtual") return "󰡨"
    return "󰛳"
  }

  function setCursor(rowId) {
    var i = rows.indexOf(rowId)
    if (i < 0) return
    cursorActive = true
    cursorIndex = i
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    if (dy === 0) return
    cursorIndex = Math.max(0, Math.min(rows.length - 1, cursorIndex + dy))
  }

  function activateCursor() {
    var id = currentRow
    if (id === "open") openApp()
    else if (id === "rescan") rescan()
    else if (id === "refresh") refresh(true)
  }

  function openApp() {
    // Pass argv directly (no shell string) so nothing depends on a shellQuote
    // helper the bar may not expose. omarchy-launch-or-focus focuses an
    // existing NetScope window or launches a new one.
    Quickshell.execDetached(["omarchy-launch-or-focus", "netscope", "uwsm-app -- " + bin])
    close()
  }

  function rescan() {
    if (scanning) return
    scanning = true
    scanProc.running = true
  }

  function copy(text) {
    if (!text) return
    Quickshell.execDetached(["sh", "-c", "printf %s " + JSON.stringify(text) + " | wl-copy"])
    lastError = ""
    flash.text = "COPIED " + text
    flashTimer.restart()
  }

  function refresh(force) {
    if (statusProc.running) return
    statusProc.command = force ? [bin, "--status", "--json", "--refresh"] : [bin, "--status", "--json"]
    statusProc.running = true
  }

  function applyStatus(text) {
    var raw = String(text || "").trim()
    if (raw === "") { lastError = "NO STATUS"; return }
    try {
      var d = JSON.parse(raw)
      ifaces = d.interfaces || []
      pub = d.public || {}
      lastScan = d.lastScan || {}
      inventory = d.inventory || {}
      loaded = true
      lastError = ""
    } catch (e) {
      lastError = "STATUS UNREADABLE"
    }
  }

  function rollTick() {
    var s = ""
    for (var i = 0; i < 44; i++) s += (Math.random() < 0.5 ? "0" : "1")
    tick = s
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    refresh(false)
    rollTick()
    if (autoScanOnOpen) rescan()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Process {
    id: statusProc
    command: [root.bin, "--status", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStatus(text) }
    stderr: StdioCollector { waitForEnd: true }
  }

  Process {
    id: scanProc
    command: [root.bin, "--scan", "--json"]
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true }
    onRunningChanged: if (!running) { root.scanning = false; root.refresh(false) }
  }

  Timer {
    interval: root.opened ? root.pollSeconds * 1000 : 120000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh(false)
  }

  Timer {
    interval: 140
    running: root.opened
    repeat: true
    onTriggered: root.rollTick()
  }

  Timer {
    id: flashTimer
    interval: 1800
    repeat: false
    onTriggered: flash.text = ""
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.opened ? "" : "NetScope"
    iconComponent: Component {
      Item {
        implicitWidth: Style.font.icon
        implicitHeight: Style.font.icon
        width: Style.font.icon
        height: Style.font.icon

        OpticalGlyph {
          anchors.fill: parent
          text: root.barGlyph
          fontFamily: root.fontFamily
          fontSize: Style.font.icon
          color: root.scanning ? Qt.darker(root.barForeground, 1.5)
                               : (root.unknownCount > 0 ? root.urgent : root.barForeground)
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.openApp()
      else if (buttonCode === Qt.MiddleButton) root.rescan()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(330))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(760))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var k = String(t).toLowerCase()
        if (k === "o") root.openApp()
        else if (k === "s") root.rescan()
        else if (k === "r") root.refresh(true)
        else if (k === "c") root.copy(root.pub.ipv4 || "")
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(10)

          // ---- header -----------------------------------------------------
          Rectangle {
            width: parent.width
            implicitHeight: headerCol.implicitHeight + Style.space(20)
            color: "transparent"
            border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.35)
            border.width: 1
            radius: Style.cornerRadius

            Column {
              id: headerCol
              anchors.centerIn: parent
              width: parent.width - Style.space(20)
              spacing: Style.space(4)

              Row {
                width: parent.width
                spacing: Style.space(8)
                Text {
                  textFormat: Text.PlainText
                  text: "◢"
                  color: root.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  textFormat: Text.PlainText
                  text: "NETSCOPE"
                  color: root.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                  font.letterSpacing: Style.space(6)
                  font.bold: true
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: flash.text !== "" ? flash.text : root.statusLine
                color: root.lastError !== "" ? root.urgent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: root.tick
                color: root.deep
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                clip: true
                elide: Text.ElideRight
              }
            }
          }

          Text { id: flash; visible: false; text: "" }

          // ---- interfaces -------------------------------------------------
          Column {
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "INTERFACES"
              foreground: root.accent
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.ifaces

              CursorSurface {
                id: ifRow
                required property var modelData
                readonly property string primary: (modelData.ipv4 && modelData.ipv4.length > 0) ? modelData.ipv4[0] : ""
                width: parent.width
                foreground: root.accent
                opacity: modelData.up ? 1.0 : 0.45
                implicitHeight: ifCol.implicitHeight + Style.spacing.rowPaddingX

                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: ifRow.primary !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: root.copy(ifRow.primary.split("/")[0])
                }

                RowLayout {
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(10)
                  spacing: Style.space(8)

                  Text {
                    textFormat: Text.PlainText
                    text: root.kindGlyph(ifRow.modelData.kind)
                    color: root.accent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.icon
                    Layout.alignment: Qt.AlignVCenter
                  }

                  ColumnLayout {
                    id: ifCol
                    Layout.fillWidth: true
                    spacing: Style.space(1)

                    RowLayout {
                      Layout.fillWidth: true
                      spacing: Style.space(6)
                      Text {
                        textFormat: Text.PlainText
                        text: ifRow.modelData.name
                        color: root.fg
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.letterSpacing: 1
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                      }
                      Text {
                        textFormat: Text.PlainText
                        text: ifRow.modelData["default"] ? "DEFAULT" : String(ifRow.modelData.state || "").toUpperCase()
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.letterSpacing: 1
                      }
                    }

                    Text {
                      textFormat: Text.PlainText
                      Layout.fillWidth: true
                      text: ifRow.primary !== "" ? ifRow.primary : "no ipv4"
                      color: ifRow.primary !== "" ? root.accent : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.subtitle
                      elide: Text.ElideRight
                    }

                    Text {
                      textFormat: Text.PlainText
                      Layout.fillWidth: true
                      visible: text !== ""
                      text: {
                        var bits = []
                        if (ifRow.modelData.ipv6 && ifRow.modelData.ipv6.length > 0) bits.push(ifRow.modelData.ipv6[0])
                        else if (ifRow.modelData.mac) bits.push(ifRow.modelData.mac)
                        if (ifRow.modelData.gateway) bits.push("gw " + ifRow.modelData.gateway)
                        return bits.join("  ·  ")
                      }
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                    }
                  }
                }
              }
            }

            Text {
              visible: root.loaded && root.ifaces.length === 0
              textFormat: Text.PlainText
              width: parent.width
              text: "no interfaces"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }
          }

          PanelSeparator { foreground: root.accent }

          // ---- public -----------------------------------------------------
          Column {
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
              text: "PUBLIC"
              foreground: root.accent
              fontFamily: root.fontFamily
            }

            CursorSurface {
              width: parent.width
              foreground: root.accent
              implicitHeight: pubCol.implicitHeight + Style.spacing.rowPaddingX

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.copy(root.pub.ipv4 || "")
              }

              ColumnLayout {
                id: pubCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(10)
                anchors.rightMargin: Style.space(10)
                spacing: Style.space(1)

                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  text: root.pub.ipv4 ? root.pub.ipv4 : (root.pub.error ? "OFFLINE" : "…")
                  color: root.pub.ipv4 ? root.accent : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.heading
                  font.bold: true
                  font.letterSpacing: 1
                  elide: Text.ElideRight
                }
                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  visible: text !== ""
                  text: root.pub.ipv6 || ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  visible: text !== ""
                  text: {
                    var bits = []
                    if (root.pub.org) bits.push(root.pub.org)
                    if (root.pub.asn) bits.push(root.pub.asn)
                    return bits.join("  ·  ")
                  }
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
                Text {
                  textFormat: Text.PlainText
                  Layout.fillWidth: true
                  visible: text !== ""
                  text: {
                    var bits = []
                    if (root.pub.city) bits.push(root.pub.city)
                    if (root.pub.region) bits.push(root.pub.region)
                    if (root.pub.country) bits.push(root.pub.country)
                    return bits.join(", ")
                  }
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }
          }

          PanelSeparator { foreground: root.accent }

          // ---- last sweep + actions -----------------------------------------
          Column {
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "LAN SWEEP"
              foreground: root.accent
              fontFamily: root.fontFamily
            }

            ActionRow {
              rowId: "open"
              glyph: "󱂬"
              title: "OPEN NETSCOPE"
              subtitle: root.lastScan.hosts > 0
                ? root.lastScan.hosts + " hosts on " + root.lastScan.network + "  ·  " + root.ageText(root.lastScan.at)
                : "no sweep yet - hosts, vendors, port probe"
              width: parent.width
              onTriggered: root.openApp()
            }

            ActionRow {
              rowId: "rescan"
              glyph: root.scanning ? "󰑓" : "󰐷"
              title: root.scanning ? "SWEEPING…" : "SWEEP LAN"
              subtitle: root.scanning ? "pinging every address, a few seconds" : "background sweep of the default network"
              enabledRow: !root.scanning
              width: parent.width
              onTriggered: root.rescan()
            }

            ActionRow {
              rowId: "refresh"
              glyph: "󰑐"
              title: "REFRESH"
              subtitle: "re-read interfaces and the public address"
              width: parent.width
              onTriggered: root.refresh(true)
            }
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "o open   s sweep   r refresh   c copy public ip"
            color: root.deep
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "DEVELOPED FOR AND BY FRIG"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 2
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
          }

          Item { width: 1; height: Style.space(2) }
        }
      }
    }
  }

  component ActionRow: CursorSurface {
    id: rowItem
    property string rowId: ""
    property string glyph: ""
    property string title: ""
    property string subtitle: ""
    property bool enabledRow: true
    signal triggered()

    hasCursor: root.cursorActive && root.currentRow === rowId && enabledRow
    foreground: root.accent
    opacity: enabledRow ? 1.0 : 0.45
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: rowItem.enabledRow ? Qt.PointingHandCursor : Qt.ArrowCursor
      enabled: rowItem.enabledRow
      onEntered: root.setCursor(rowItem.rowId)
      onClicked: rowItem.triggered()
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: rowItem.glyph
        color: root.accent
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: rowContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: rowItem.title
          color: root.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.letterSpacing: 1
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: rowItem.subtitle
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
