"""Lossless, offline view of SpringBoard responses; no device imports or writes."""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import plistlib
from typing import Any


def snapshot_bytes(value: Any) -> bytes:
    """Keep native dates, binary values and unknown fields in a property list."""
    return plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=True)


def snapshot_digest(value: Any) -> str:
    return hashlib.sha256(snapshot_bytes(value)).hexdigest()


def item_kind(item: Any) -> str:
    if not isinstance(item, dict):
        return 'identifier' if isinstance(item, str) and item else 'unknown'
    if (item.get('elementType') == 'widget' or 'widgetIdentifier' in item
            or 'elements' in item or item.get('iconType') == 'custom'):
        return 'widget_stack' if 'elements' in item else 'widget'
    if item.get('listType') == 'folder' or item.get('iconType') == 'folder':
        return 'folder'
    if (item.get('elementType') not in (None, 'app')
            or item.get('iconType') not in (None, 'app')
            or item.get('listType') is not None or 'gridSize' in item):
        return 'unknown'
    if 'webClipURL' in item:
        return 'web_clip'
    display, bundle = item.get('displayIdentifier'), item.get('bundleIdentifier')
    if (item.get('iconType') == 'app' and 'iconLists' in item
            or display and bundle and display != bundle):
        return 'special_app'
    if 'iconLists' in item:
        return 'unknown'
    return 'app' if display or bundle else 'unknown'


def item_identifier(item: Any) -> str | None:
    """A display instance wins over its owning application's bundle ID."""
    if isinstance(item, str):
        return item or None
    if isinstance(item, dict):
        for key in ('displayIdentifier', 'bundleIdentifier', 'webClipURL'):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return None


@dataclass(frozen=True)
class LayoutSnapshot:
    """Retain the raw response; views are copies so editing cannot mutate the source."""
    _raw: Any

    @classmethod
    def from_raw(cls, raw):
        if isinstance(raw, list):
            if len(raw) < 2 or not all(isinstance(page, list) for page in raw):
                raise ValueError('format 2 must contain Dock and page arrays')
        elif isinstance(raw, dict):
            if not isinstance(raw.get('buttonBar'), list) or not isinstance(raw.get('iconLists'), list):
                raise ValueError('dictionary layout must contain buttonBar and iconLists arrays')
            if not all(isinstance(page, list) for page in raw['iconLists']):
                raise ValueError('dictionary page must be an array')
        else:
            raise ValueError('unrecognized layout response')
        snapshot_bytes(raw)  # Check the complete tree is representable, without stripping fields.
        return cls(deepcopy(raw))

    def raw_copy(self):
        return deepcopy(self._raw)

    def to_bytes(self):
        return snapshot_bytes(self._raw)

    def describe(self):
        if isinstance(self._raw, list):
            dock, pages = self._raw[0], self._raw[1:]
        else:
            dock, pages = self._raw['buttonBar'], self._raw['iconLists']
        counts = Counter()
        instances = []
        unknown_paths = []

        def visit(item, path):
            kind = item_kind(item)
            counts[kind] += 1
            if kind in ('widget', 'widget_stack', 'special_app'):
                entry = {'path': path, 'kind': kind, 'identifier': item_identifier(item)}
                for key in ('gridSize', 'containerBundleIdentifier', 'widgetIdentifier',
                            'uniqueIdentifier', 'userSelectedElementIdentifier'):
                    if key in item:
                        entry[key] = deepcopy(item[key])
                if kind == 'widget_stack':
                    elements = item['elements']
                    entry['element_count'] = len(elements) if isinstance(elements, list) else None
                instances.append(entry)
            elif kind == 'unknown':
                unknown_paths.append(path)
            if kind == 'folder':
                nested = item.get('iconLists')
                if not isinstance(nested, list):
                    unknown_paths.append(path + '.iconLists')
                    return
                for pi, page in enumerate(nested):
                    if not isinstance(page, list):
                        unknown_paths.append(f'{path}.iconLists[{pi}]')
                        continue
                    for ii, child in enumerate(page):
                        visit(child, f'{path}.iconLists[{pi}][{ii}]')

        for pi, page in enumerate([dock, *pages]):
            for ii, item in enumerate(page):
                visit(item, f'layout[{pi}][{ii}]')
        return {'home_screen_pages': len(pages), 'dock_items': len(dock),
                'counts': dict(counts), 'instances': instances, 'unknown_paths': unknown_paths,
                'spatial_positions_verified': False, 'widget_settings_verified': False}
