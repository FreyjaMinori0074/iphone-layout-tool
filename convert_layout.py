#!/usr/bin/env python3
"""Inspect SpringBoard snapshots or convert supported items to cfgutil JSON.

Conversion refuses unsupported objects rather than silently losing layout data.
Inspection preserves the source and never communicates with a device.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


class LayoutError(ValueError):
    """A snapshot cannot be represented safely in the supported format."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__('\n'.join(issues))


def get_id(item):
    if isinstance(item, str):
        return item if item.strip() else None
    if isinstance(item, dict):
        for key in ('webClipURL', 'bundleIdentifier', 'displayIdentifier'):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def classify(item):
    """Classify before extracting identifiers: widgets may also have iconLists."""
    if item is None or item == {}:
        return 'placeholder'
    if isinstance(item, str):
        return 'app_or_web_clip' if get_id(item) else 'placeholder'
    if not isinstance(item, dict):
        return 'unknown'
    if item.get('elementType') == 'widget' or 'widgetIdentifier' in item:
        return 'widget'
    if item.get('elementType') not in (None, 'app', 'folder'):
        return 'unknown'
    if item.get('iconType') not in (None, 'app'):
        return 'unknown'
    # UUID-backed app instances must not collapse to their parent bundle ID.
    if item.get('iconType') == 'app' and 'iconLists' in item:
        return 'special_app'
    if item.get('listType') == 'folder':
        return 'folder'
    if item.get('listType') is not None:
        return 'unknown'
    if 'iconLists' in item:
        # Accept legacy folder snapshots only when their structure is explicit.
        if (item.get('iconType') is None and not get_id(item)
                and isinstance(item.get('displayName'), str)
                and isinstance(item['iconLists'], list) and item['iconLists']):
            return 'folder'
        return 'unknown'
    if 'gridSize' in item or 'containerBundleIdentifier' in item:
        return 'unknown'
    if 'webClipURL' in item and get_id({'webClipURL': item['webClipURL']}):
        return 'web_clip'
    if get_id(item):
        return 'app'
    return 'unknown'


def _convert_item(item, location, issues, in_folder=False):
    kind = classify(item)
    reasons = {
        'widget': 'widget size, instance and configuration cannot be preserved by this converter',
        'special_app': 'special app instance cannot safely collapse to a bundle identifier',
        'placeholder': 'empty position cannot be preserved by this converter',
        'unknown': 'unknown layout object; refusing to discard fields',
    }
    if kind in reasons:
        issues.append(f'{location}: {reasons[kind]}')
        return None
    if kind != 'folder':
        return get_id(item)
    if in_folder:
        issues.append(f'{location}: nested folders are unsupported')
        return None
    name = item.get('displayName')
    pages = item.get('iconLists')
    if not isinstance(name, str) or not name.strip():
        issues.append(f'{location}: folder must have a nonempty displayName')
        return None
    if not isinstance(pages, list) or not pages:
        issues.append(f'{location}: folder must contain at least one nonempty page')
        return None
    converted_pages = []
    for index, page in enumerate(pages):
        path = f'{location}.iconLists[{index}]'
        if not isinstance(page, list) or not page:
            issues.append(f'{path}: folder page must be a nonempty array')
            continue
        converted_pages.append([
            _convert_item(child, f'{path}[{i}]', issues, in_folder=True)
            for i, child in enumerate(page)
        ])
    return [name, *converted_pages[0]] if len(pages) == 1 and converted_pages else [name, *converted_pages]


def item_to_cfgutil(item):
    issues = []
    result = _convert_item(item, 'item', issues)
    if issues:
        raise LayoutError(issues)
    return result


def convert_layout(pages):
    if not isinstance(pages, list) or len(pages) < 2:
        raise LayoutError(['layout: expected an array containing Dock and at least one page'])
    issues = []
    result = []
    for pi, page in enumerate(pages):
        if not isinstance(page, list):
            issues.append(f'layout[{pi}]: page must be an array')
            continue
        if pi == 0 and len(page) > 4:
            issues.append('layout[0]: Dock contains more than four items')
        result.append([_convert_item(item, f'layout[{pi}][{ii}]', issues)
                       for ii, item in enumerate(page)])
    if issues:
        raise LayoutError(issues)
    return result


def inspect_layout(pages):
    """Return classifications and instance metadata without changing the input."""
    if not isinstance(pages, list) or len(pages) < 2:
        raise LayoutError(['layout: expected an array containing Dock and at least one page'])
    counts = Counter()
    entries = []

    def visit(item, path):
        kind = classify(item)
        counts[kind] += 1
        entry = {'path': path, 'kind': kind}
        if isinstance(item, dict):
            for key in ('displayName', 'displayIdentifier', 'bundleIdentifier',
                        'containerBundleIdentifier', 'widgetIdentifier', 'gridSize',
                        'userSelectedElementIdentifier'):
                if key in item:
                    entry[key] = item[key]
        entries.append(entry)
        if kind == 'folder' and isinstance(item.get('iconLists'), list):
            for pi, page in enumerate(item['iconLists']):
                if isinstance(page, list):
                    for ii, child in enumerate(page):
                        visit(child, f'{path}.iconLists[{pi}][{ii}]')

    for pi, page in enumerate(pages):
        if not isinstance(page, list):
            raise LayoutError([f'layout[{pi}]: page must be an array'])
        for ii, item in enumerate(page):
            visit(item, f'layout[{pi}][{ii}]')
    try:
        convert_layout(pages)
        issues = []
    except LayoutError as exc:
        issues = exc.issues
    return {'home_screen_pages': len(pages) - 1, 'counts': dict(counts),
            'convertible': not issues, 'conversion_issues': issues, 'entries': entries}


def print_summary(layout):
    for i, page in enumerate(layout):
        label = 'Dock' if i == 0 else f'Page {i}'
        folders = sum(isinstance(x, list) for x in page)
        print(f'{label}: {len(page) - folders} icons, {folders} folders', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='Raw pymobiledevice3 JSON snapshot')
    parser.add_argument('--output', '-o', help='Output file; untouched if conversion fails')
    parser.add_argument('--summary', '-s', action='store_true')
    parser.add_argument('--inspect', action='store_true', help='Inspect without converting; outputs a report, NOT a pushable layout')
    args = parser.parse_args()
    try:
        if args.output and Path(args.input).resolve() == Path(args.output).resolve():
            raise ValueError('input and output must differ; preserve the original snapshot')
        with open(args.input, encoding='utf-8') as source:
            pages = json.load(source)
        result = inspect_layout(pages) if args.inspect else convert_layout(pages)
        if args.summary and not args.inspect:
            print_summary(result)
        output = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            Path(args.output).write_text(output, encoding='utf-8')
            print(f'Wrote {args.output}', file=sys.stderr)
        else:
            print(output, end='')
    except (LayoutError, ValueError, OSError) as exc:
        print(f'Refused: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
