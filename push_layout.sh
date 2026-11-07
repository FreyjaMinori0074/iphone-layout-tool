#!/bin/bash
# Push a cfgutil-format JSON layout to a connected iPhone.
#
# Usage:
#   ./push_layout.sh layout.json
#   ./push_layout.sh layout.json --backup  # saves current layout first
#
# Requirements:
#   - Apple Configurator installed
#   - iPhone connected via USB
#   - pymobiledevice3 (for --backup)

set -euo pipefail

CFGUTIL="/Applications/Apple Configurator.app/Contents/MacOS/cfgutil"
LAYOUT_FILE="${1:-}"
DO_BACKUP=false

if [ -z "$LAYOUT_FILE" ]; then
    echo "Usage: $0 <layout.json> [--backup]"
    echo ""
    echo "Push a home screen layout to a connected iPhone."
    echo ""
    echo "Options:"
    echo "  --backup    Save current layout before pushing"
    exit 1
fi

# Check for --backup flag
for arg in "$@"; do
    if [ "$arg" = "--backup" ]; then
        DO_BACKUP=true
    fi
done

# Verify cfgutil exists
if [ ! -f "$CFGUTIL" ]; then
    echo "Error: Apple Configurator not found at $CFGUTIL"
    echo "Install it from the Mac App Store."
    exit 1
fi

# Verify layout file exists
if [ ! -f "$LAYOUT_FILE" ]; then
    echo "Error: Layout file not found: $LAYOUT_FILE"
    exit 1
fi

# Validate the cfgutil structure before touching an app or connected device.
# Pass the filename as an argument, never interpolate it into Python source.
if ! python3 - "$LAYOUT_FILE" <<'PYVALIDATE'
import json
import sys


def icon(value):
    return isinstance(value, str) and bool(value.strip())


def folder(value):
    if not isinstance(value, list) or len(value) < 2 or not icon(value[0]):
        return False
    contents = value[1:]
    return all(icon(x) for x in contents) or all(
        isinstance(page, list) and bool(page) and all(icon(x) for x in page)
        for page in contents
    )


try:
    with open(sys.argv[1], encoding='utf-8') as source:
        layout = json.load(source)
    if not isinstance(layout, list) or len(layout) < 2:
        raise ValueError('expected Dock and at least one page; inspection reports are not layouts')
    for pi, page in enumerate(layout):
        if not isinstance(page, list):
            raise ValueError(f'page {pi} must be an array')
        if pi == 0 and len(page) > 4:
            raise ValueError('Dock contains more than four items')
        for ii, item in enumerate(page):
            if not (icon(item) or folder(item)):
                raise ValueError(f'page {pi}, item {ii}: invalid icon or folder')
except (OSError, ValueError) as exc:
    print(f'Error: invalid cfgutil layout: {exc}', file=sys.stderr)
    sys.exit(1)
PYVALIDATE
then
    exit 1
fi

# Kill Apple Configurator GUI if running (it locks the device)
if pgrep -f "Apple Configurator" > /dev/null 2>&1; then
    echo "Closing Apple Configurator GUI (it locks the device)..."
    pkill -f "Apple Configurator" 2>/dev/null || true
    sleep 2
fi

# Backup current layout if requested
if [ "$DO_BACKUP" = true ]; then
    BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).json"
    echo "Backing up current layout to $BACKUP_FILE..."
    if command -v pymobiledevice3 &> /dev/null; then
        pymobiledevice3 springboard state get > "$BACKUP_FILE"
        echo "Backup saved (pymobiledevice3 format). Convert with: python3 convert_layout.py $BACKUP_FILE"
    else
        echo "Warning: pymobiledevice3 not found, skipping backup"
    fi
fi

# Push the layout
echo "Pushing layout from $LAYOUT_FILE..."
"$CFGUTIL" set-icon-layout --force "$LAYOUT_FILE"

echo ""
echo "Done. Check your phone."
