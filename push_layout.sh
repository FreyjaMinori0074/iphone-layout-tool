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

# Verify it's valid JSON
if ! python3 -c "import json; json.load(open('$LAYOUT_FILE'))" 2>/dev/null; then
    echo "Error: $LAYOUT_FILE is not valid JSON"
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
