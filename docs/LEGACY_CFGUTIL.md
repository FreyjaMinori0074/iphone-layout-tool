# 原项目 cfgutil 路线（历史说明）

以下保留原仓库 README，作为研究背景。它的兼容性和协议结论不代表当前验证结果。
含组件桌面使用当前 [原生链路](END_TO_END.md)，旧格式不能完整描述小组件。

---

# iPhone Home Screen Layout Tool

Programmatically rearrange iPhone home screen icons from your Mac. No jailbreak, no MDM/supervision, no manual dragging.

## The Discovery

The internet consensus (and Apple's own documentation) says you **cannot** programmatically modify iPhone home screen layouts on consumer (non-supervised) devices. Every known method was blocked by iOS 13-15:

- `setIconState` via SpringBoardServices — silently ignored
- `IconState.plist` backup manipulation — no longer works
- libimobiledevice `sbmanager` — marked INACTIVE
- AnyTrans/iMazing Home Screen Manager — broken since iOS 15

**What actually works:** Apple's own `cfgutil` CLI (bundled inside Apple Configurator.app) has a `set-icon-layout` command that uses the AirTraffic sync protocol instead of `setIconState`. This bypasses the restriction entirely and works on non-supervised consumer iPhones.

## Requirements

- macOS with [Apple Configurator](https://apps.apple.com/app/apple-configurator/id1037126344) installed (free from Mac App Store)
- iPhone connected via USB cable
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (`pip install pymobiledevice3`)
- Python 3

## Quick Start

### 1. Read the current layout

```bash
pymobiledevice3 springboard state get > current_layout.json
```

This gives you the full layout in pymobiledevice3's format (dicts with `bundleIdentifier`, `displayName`, etc).

### 2. Convert to cfgutil format

```bash
python3 convert_layout.py current_layout.json > cfgutil_layout.json
```

cfgutil expects a simpler format: an array of arrays where items are bundle ID strings.

### 3. Edit the layout

Edit `cfgutil_layout.json` — it's just JSON. See [Layout Format](#layout-format) below.

### 4. Close Apple Configurator GUI

**Important:** cfgutil can't connect while the GUI app holds the device.

```bash
osascript -e 'tell application "Apple Configurator" to quit' 2>/dev/null
pkill -f "Apple Configurator" 2>/dev/null
```

### 5. Push the layout

```bash
/Applications/Apple\ Configurator.app/Contents/MacOS/cfgutil set-icon-layout --force cfgutil_layout.json
```

The `--force` flag allows referencing apps that might not be installed.

### 6. Verify

Check your phone. The layout updates instantly.

## Layout Format

cfgutil expects a JSON array of arrays:

```json
[
  ["dock.app1", "dock.app2", "dock.app3", "dock.app4"],
  ["page1.app1", "page1.app2", ["Folder Name", "folder.app1", "folder.app2"]],
  ["page2.app1", "page2.app2"]
]
```

- **First array** = Dock (max 4 items)
- **Each subsequent array** = A home screen page
- **Strings** = Bundle identifiers (e.g., `com.apple.mobilesafari`)
- **Web clips** = URLs (e.g., `https://example.com`)
- **Single-page folders** = `["Folder Name", "app1.bundle.id", "app2.bundle.id"]`
- **Multi-page folders** = `["Folder Name", ["page1.app1", "page1.app2"], ["page2.app1", "page2.app2"]]`

## Common Operations

### Swap two apps

```python
import json

with open('cfgutil_layout.json') as f:
    layout = json.load(f)

# Swap positions 0 and 1 on page 1
layout[1][0], layout[1][1] = layout[1][1], layout[1][0]

with open('cfgutil_layout.json', 'w') as f:
    json.dump(layout, f, indent=2)
```

### Move an app into a folder

```python
# Remove app from page
app_id = 'com.google.tasks'
layout[1] = [x for x in layout[1] if x != app_id]

# Add to existing folder (find it first)
for item in layout[1]:
    if isinstance(item, list) and item[0] == 'Productivity':
        item.append(app_id)
        break
```

### Create a new folder

```python
# Single-page folder
new_folder = ["AI Tools", "com.openai.chat", "com.anthropic.claude", "com.google.gemini"]
layout[1].append(new_folder)
```

## Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| "device already in use" | Apple Configurator GUI is open | Close it first (`pkill -f "Apple Configurator"`) |
| `cfgutil: command not found` | Not in PATH | Use full path: `/Applications/Apple Configurator.app/Contents/MacOS/cfgutil` |
| `get-icon-layout` crashes | Bug with widgets/new icon types | Use `pymobiledevice3 springboard state get` instead |
| Apps missing after push | Bundle ID wrong or app not installed | Use `--force` flag, verify bundle IDs with `ideviceinstaller list --all` |
| Folders empty | Empty folder arrays `["Name"]` | Ensure at least one app in each folder |

## How It Works

Apple Configurator's `cfgutil` uses the **AirTraffic protocol** (the iTunes/Finder sync protocol) to communicate with the device, rather than the `SpringBoardServices` lockdown service. Apple locked down `setIconState` for consumer devices in iOS 13-15, but the AirTraffic pathway was not restricted in the same way.

The flow:
1. cfgutil connects via usbmuxd
2. Establishes an AirTraffic session with the device
3. Sends the layout as a sync operation
4. Device applies it immediately

## Files

| File | Purpose |
|------|---------|
| `convert_layout.py` | Convert pymobiledevice3 format to cfgutil format |
| `push_layout.sh` | One-command layout push with safety checks |
| `README.md` | This file |

## Discovery Context

This was discovered on 2026-01-31 through a combination of:
1. Protocol sniffing Apple Configurator (which revealed it uses AirTraffic, not SpringBoardServices)
2. Finding `cfgutil` CLI bundled inside Apple Configurator.app
3. Testing `set-icon-layout` on a non-supervised iPhone 15 running iOS 17

Previous research session (2026-01-26) had concluded programmatic layout changes were impossible. The key insight was that `cfgutil` uses a completely different protocol path than what all the third-party tools (pymobiledevice3, libimobiledevice) were attempting.
