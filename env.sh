# Source from the project directory: source ./env.sh
# Internal-disk virtualenv avoids ExFAT AppleDouble metadata issues.
IPHONE_LAYOUT_VENV="${IPHONE_LAYOUT_VENV:-$HOME/.local/share/iphone-layout-tool/venv}"
if [ -f "$IPHONE_LAYOUT_VENV/bin/activate" ]; then
    . "$IPHONE_LAYOUT_VENV/bin/activate"
else
    echo "Environment not found: $IPHONE_LAYOUT_VENV" >&2
    return 1
fi
