"""Pure layout planning and readback comparison. No device or network access.

The experimental root-archive format is described in docs/WIDGET_RESEARCH.md.
Only references to existing objects are editable in this first end-to-end path.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib

from layout_snapshot import LayoutSnapshot, item_identifier, item_kind, snapshot_digest

VOLATILE = {'iconModDate', 'bundleVersion'}
CONFIG_IDS = {'uniqueIdentifier', 'userSelectedElementIdentifier'}


def pages_of(state):
    LayoutSnapshot.from_raw(state)
    return state if isinstance(state, list) else [state['buttonBar'], *state['iconLists']]


def require_text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{where}: expected a nonempty string')
    return value


def inventory_view(apps):
    if not isinstance(apps, dict) or not apps:
        raise ValueError('A nonempty app inventory is required')
    result = {}
    for ident, meta in apps.items():
        require_text(ident, 'app identifier')
        if not isinstance(meta, dict) or meta.get('ApplicationType') not in ('User', 'System'):
            raise ValueError(f'{ident}: unrecognized ApplicationType')
        result[ident] = {'ApplicationType': meta['ApplicationType'],
                         'hidden': 'hidden' in (meta.get('SBAppTags') or []),
                         'placeholder': bool(meta.get('IsPlaceholder'))}
    return result


def walk(state):
    def visit(item, path, nested=False):
        yield path, item, nested
        if item_kind(item) == 'folder':
            lists = item.get('iconLists')
            if not isinstance(lists, list):
                raise ValueError(f'{path}: folder pages are missing')
            for pi, page in enumerate(lists):
                if not isinstance(page, list):
                    raise ValueError(f'{path}: invalid folder page')
                for ii, child in enumerate(page):
                    yield from visit(child, f'{path}.iconLists[{pi}][{ii}]', True)
    for pi, page in enumerate(pages_of(state)):
        for ii, item in enumerate(page):
            yield from visit(item, f'layout[{pi}][{ii}]')


def validate_state(state, metrics):
    sections = pages_of(state)
    columns = metrics.get('homeScreenIconColumns')
    rows = metrics.get('homeScreenIconRows')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 or int(x) != x
           for x in (columns, rows)):
        raise ValueError('Valid device grid metrics are required')
    if (int(columns), int(rows)) != (4, 6):
        raise ValueError('This experimental writer currently supports the observed 4 by 6 iPhone grid')
    if len(sections[0]) > 4:
        raise ValueError('Dock contains more than four objects')
    shapes = {'small': 4, 'medium': 8, 'large': 16}
    for pi, page in enumerate(sections):
        used = 0
        for item in page:
            kind = item_kind(item)
            if pi == 0 and kind in ('widget', 'widget_stack'):
                raise ValueError('Widgets cannot be put in the Dock')
            if kind in ('widget', 'widget_stack'):
                if item.get('gridSize') not in shapes:
                    raise ValueError('Unverified widget size; refusing to guess its footprint')
                used += shapes[item['gridSize']]
            else:
                used += 1
        if pi and used > int(columns * rows):
            raise ValueError(f'Page {pi} exceeds the observed grid capacity')
    seen = set()
    for path, item, nested in walk(state):
        kind = item_kind(item)
        if kind == 'unknown':
            raise ValueError(f'{path}: unknown object is not writable')
        if kind == 'folder':
            if nested:
                raise ValueError(f'{path}: nested folder is unsupported')
            require_text(item.get('displayName'), path + '.displayName')
            if not item.get('iconLists') or any(not page for page in item['iconLists']):
                raise ValueError(f'{path}: empty folder/page is unsupported')
            if len(item['iconLists']) > 15 or any(len(page) > 9 for page in item['iconLists']):
                raise ValueError(f'{path}: folder exceeds the observed 3 by 3 / 15-page limits')
        else:
            ident = require_text(item_identifier(item), path + '.identifier')
            if ident in seen:
                raise ValueError(f'{path}: duplicate instance identifier {ident}')
            seen.add(ident)
            if nested and kind in ('widget', 'widget_stack'):
                raise ValueError(f'{path}: widget inside folder is unsupported')
            if kind in ('widget', 'widget_stack'):
                if item.get('iconLists') not in (None, []):
                    raise ValueError(f'{path}: unexpected widget iconLists')
                if kind == 'widget_stack':
                    elements = item.get('elements')
                    if not isinstance(elements, list) or not elements or not all(isinstance(e, dict) for e in elements):
                        raise ValueError(f'{path}: stack elements are invalid')
    return state


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def state_fingerprint(state):
    """Strict precondition: retain all nonvolatile fields, including unknown metadata."""
    return snapshot_digest(_stable(state))


def placed_app_ids(state):
    # Widget extensions, web clips and duplicate icon instances are not parent apps.
    return {item_identifier(x) for _, x, _ in walk(state) if item_kind(x) in ('app', 'identifier')}


def root_archive(state, apps):
    inventory = inventory_view(apps)
    missing = placed_app_ids(state) - set(inventory)
    if missing:
        raise ValueError('Some visible app identifiers are absent from the inventory: ' + ', '.join(sorted(missing)))
    changes = []

    def encode(item, path):
        kind = item_kind(item)
        if kind in ('app', 'identifier', 'web_clip', 'special_app'):
            return require_text(item_identifier(item), path)
        if kind == 'folder':
            result = deepcopy(item)
            result.pop('iconType', None)
            result['listType'] = 'folder'
            result['iconLists'] = [[encode(x, f'{path}.iconLists[{pi}][{ii}]') for ii, x in enumerate(page)]
                                   for pi, page in enumerate(item['iconLists'])]
            return result
        if kind in ('widget', 'widget_stack'):
            result = deepcopy(item)
            result.pop('iconLists', None)
            if kind == 'widget' and not result.get('uniqueIdentifier') and result.get('userSelectedElementIdentifier'):
                result['uniqueIdentifier'] = result['userSelectedElementIdentifier']
                changes.append({'path': path, 'field': 'uniqueIdentifier',
                                'reason': 'reuse the returned single-widget configuration element ID'})
            return result
        raise ValueError(f'{path}: unknown object cannot be serialized')

    sections = pages_of(state)
    # Hidden/system records also stay off Home Screen rather than being surfaced by omission.
    ignored = sorted(set(inventory) - placed_app_ids(state))
    if isinstance(state, dict):
        raise ValueError('The first writer accepts native format-2 list snapshots only')
    payload = {'buttonBar': [encode(x, f'layout[0][{i}]') for i, x in enumerate(sections[0])],
               'iconLists': [[encode(x, f'layout[{pi}][{ii}]') for ii, x in enumerate(page)]
                             for pi, page in enumerate(sections[1:], 1)],
               'ignored': ignored}
    return payload, changes


def _shape(item):
    kind = item_kind(item)
    if kind == 'folder':
        return ['folder', item.get('displayName'), [[_shape(x) for x in p] for p in item['iconLists']]]
    if kind in ('widget', 'widget_stack'):
        # Fields not returned consistently are compared separately, never assumed equal.
        data = {k: deepcopy(v) for k, v in item.items()
                if k not in VOLATILE | CONFIG_IDS | {'iconLists', 'elements'}}
        if 'elements' in item:
            data['elements'] = [{k: deepcopy(v) for k, v in e.items() if k not in VOLATILE | CONFIG_IDS}
                                for e in item['elements']]
        return [kind, data]
    return [kind, item_identifier(item)]


def compare_layout(expected, observed):
    left, right = pages_of(expected), pages_of(observed)
    structural = [[_shape(x) for x in p] for p in left] == [[_shape(x) for x in p] for p in right]
    config_issues = []
    if structural:
        expected_items = list(walk(expected)); observed_items = list(walk(observed))
        for (path, a, _), (_, b, _) in zip(expected_items, observed_items):
            if item_kind(a) not in ('widget', 'widget_stack'):
                continue
            # Check the root and every stack element. Stack top-choice is separate from element identity.
            pairs = [(path, a, b)]
            if item_kind(a) == 'widget_stack':
                pairs += [(f'{path}.elements[{i}]', x, y) for i, (x, y) in enumerate(zip(a['elements'], b['elements']))]
                if a.get('userSelectedElementIdentifier') != b.get('userSelectedElementIdentifier'):
                    config_issues.append({'path': path, 'status': 'different', 'field': 'stack_selection'})
            for location, x, y in pairs:
                if location == path and item_kind(a) == 'widget_stack':
                    exp, got = x.get('uniqueIdentifier'), y.get('uniqueIdentifier')
                else:
                    exp = x.get('uniqueIdentifier') or x.get('userSelectedElementIdentifier')
                    got = y.get('uniqueIdentifier') or y.get('userSelectedElementIdentifier')
                if exp and got and exp != got:
                    config_issues.append({'path': location, 'status': 'different', 'field': 'configuration_identity'})
                elif exp and not got:
                    config_issues.append({'path': location, 'status': 'not_returned', 'field': 'configuration_identity'})
    return {'structure_matches': structural,
            'configuration_identity': config_issues,
            'configuration_identity_conflict': any(x['status'] == 'different' for x in config_issues),
            'widget_contents_verified': False, 'spatial_positions_verified': False,
            'app_library_membership_verified': False}


def apply_operations(source, operations):
    """Small initial operation vocabulary; arbitrary objects/payloads are never accepted."""
    result = deepcopy(source)
    if not isinstance(result, list):
        raise ValueError('Operations currently require a format-2 list')
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError('The first chain trial requires exactly one swap operation')
    for op in operations:
        if not isinstance(op, dict) or set(op) != {'type', 'page', 'first', 'second'} or op['type'] != 'swap':
            raise ValueError('Unsupported operation; expected a swap of existing references')
        pi, i, j = op['page'], op['first'], op['second']
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (pi, i, j)):
            raise ValueError('Swap indexes must be integers')
        if not 0 <= pi < len(result) or any(not 0 <= v < len(result[pi]) for v in (i, j)) or i == j:
            raise ValueError('Swap indexes are out of range or identical')
        result[pi][i], result[pi][j] = result[pi][j], result[pi][i]
    return result


def edit_summary(source, target):
    changes = []
    for pi, (a, b) in enumerate(zip(pages_of(source), pages_of(target))):
        for ii, (x, y) in enumerate(zip(a, b)):
            if snapshot_digest(x) != snapshot_digest(y):
                def label(item):
                    return item.get('displayName') or item_identifier(item) if isinstance(item, dict) else item
                changes.append({'path': f'layout[{pi}][{ii}]', 'before': label(x), 'after': label(y)})
    return changes


def create_plan(source, apps, identity, metrics, operations):
    for key in ('UniqueDeviceID', 'ProductType', 'ProductVersion', 'BuildVersion'):
        require_text(identity.get(key), key)
    validate_state(source, metrics)
    target = apply_operations(source, operations)
    validate_state(target, metrics)
    # Inventory projections avoid storing app SDK credentials unrelated to planning.
    inventory = inventory_view(apps)
    payload, transforms = root_archive(target, apps)
    return {'schema_version': 1, 'kind': 'experimental_layout_plan',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'device': {k: identity[k] for k in ('UniqueDeviceID', 'ProductType', 'ProductVersion', 'BuildVersion')},
            'source_state': deepcopy(source), 'target_state': target, 'operations': deepcopy(operations),
            'source_fingerprint': state_fingerprint(source), 'inventory': inventory,
            'inventory_fingerprint': snapshot_digest(inventory), 'metrics': deepcopy(metrics),
            'payload': payload, 'field_transforms': transforms, 'changes': edit_summary(source, target),
            'limitations': ['Whole-layout import can alter gaps even on pages not edited.',
                            'Widget contents, original gaps and exact App Library membership remain unverified.',
                            'Only a matching full readback establishes structural success.']}


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') != 1 or plan.get('kind') != 'experimental_layout_plan':
        raise ValueError('Not a supported layout plan')
    # Rebuild all derived fields so even locally edited/LLM-produced data cannot bypass validation.
    apps = {k: {'ApplicationType': v['ApplicationType'], 'SBAppTags': ['hidden'] if v['hidden'] else [],
                'IsPlaceholder': v['placeholder']} for k, v in plan['inventory'].items()}
    rebuilt = create_plan(plan['source_state'], apps, plan['device'], plan['metrics'], plan['operations'])
    for key in rebuilt:
        if key != 'created_at' and rebuilt[key] != plan.get(key):
            raise ValueError('Plan derived field is inconsistent: ' + key)
    return plan


def load_capture(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('status') != 'complete' or not manifest.get('observed_layout_response_equal') or not manifest.get('observed_identity_stable'):
        raise ValueError('A complete stable native capture is required')
    result = {}
    for key, name in {'source': 'layout-v2.plist', 'apps': 'apps.plist',
                      'identity': 'identity-before.plist', 'metrics': 'metrics.plist'}.items():
        raw = (directory / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['artifacts'][name]['sha256']:
            raise ValueError('Capture artifact checksum mismatch: ' + name)
        result[key] = plistlib.loads(raw)
    return result
