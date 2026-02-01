#!/usr/bin/env python3
"""
Convert pymobiledevice3 springboard layout to cfgutil JSON format.

Usage:
    pymobiledevice3 springboard state get > current_layout.json
    python3 convert_layout.py current_layout.json > cfgutil_layout.json
    python3 convert_layout.py current_layout.json --output cfgutil_layout.json
"""

import json
import sys
import argparse


def get_id(item):
    """Extract the identifier from a pymobiledevice3 layout item."""
    if isinstance(item, dict):
        if 'webClipURL' in item:
            return item['webClipURL']
        if 'bundleIdentifier' in item:
            return item['bundleIdentifier']
        if 'displayIdentifier' in item:
            return item['displayIdentifier']
    return None


def item_to_cfgutil(item):
    """Convert a single pymobiledevice3 item to cfgutil format."""
    if isinstance(item, dict):
        # Folder with icon pages
        if 'iconLists' in item:
            folder_name = item.get('displayName', 'Folder')
            icon_pages = item['iconLists']

            if len(icon_pages) == 1:
                # Single-page folder: ["Name", "app1", "app2", ...]
                result = [folder_name]
                for sub in icon_pages[0]:
                    sid = get_id(sub)
                    if sid:
                        result.append(sid)
                return result
            else:
                # Multi-page folder: ["Name", ["app1", "app2"], ["app3", "app4"]]
                result = [folder_name]
                for page in icon_pages:
                    folder_page = []
                    for sub in page:
                        sid = get_id(sub)
                        if sid:
                            folder_page.append(sid)
                    if folder_page:
                        result.append(folder_page)
                return result

        # Regular app or web clip
        return get_id(item)

    if isinstance(item, str):
        return item
    return None


def convert_layout(pages):
    """Convert full pymobiledevice3 layout to cfgutil format."""
    cfgutil_layout = []
    for page in pages:
        items = page if isinstance(page, list) else []
        cfgutil_page = []
        for item in items:
            converted = item_to_cfgutil(item)
            if converted is not None:
                cfgutil_page.append(converted)
        cfgutil_layout.append(cfgutil_page)
    return cfgutil_layout


def print_summary(layout):
    """Print a human-readable summary of the layout."""
    print(f"Pages: {len(layout)}", file=sys.stderr)
    for i, page in enumerate(layout):
        label = "Dock" if i == 0 else f"Page {i}"
        folders = sum(1 for x in page if isinstance(x, list))
        apps = sum(1 for x in page if isinstance(x, str))
        print(f"  {label}: {apps} apps, {folders} folders", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Convert pymobiledevice3 layout to cfgutil format')
    parser.add_argument('input', help='Input JSON file (pymobiledevice3 format)')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    parser.add_argument('--summary', '-s', action='store_true', help='Print layout summary to stderr')
    args = parser.parse_args()

    with open(args.input) as f:
        pages = json.load(f)

    layout = convert_layout(pages)

    if args.summary:
        print_summary(layout)

    output = json.dumps(layout, indent=2)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(output + '\n')
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
