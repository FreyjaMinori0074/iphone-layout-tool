import asyncio
from copy import deepcopy
from datetime import datetime
import json
import argparse
from contextlib import redirect_stdout
import io
import subprocess
import sys
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from layout_plan import (apply_operations, compare_layout, create_plan, inventory_view,
                         load_capture, root_archive, state_fingerprint, validate_plan, validate_state, walk)
from layout_snapshot import item_identifier, item_kind, snapshot_digest
from layout_transaction import execute, load_transaction, UsbWriter, device_lock
from capture_snapshot import collect
from layout_tool import run_device

UDID = 'test-phone'
IDENTITY = {'UniqueDeviceID': UDID, 'ProductType': 'iPhone18,2',
            'ProductVersion': '27.2', 'BuildVersion': '24B119'}
METRICS = {'homeScreenIconColumns': 4.0, 'homeScreenIconRows': 6.0}


def app(ident):
    return {'bundleIdentifier': ident, 'displayIdentifier': ident, 'displayName': ident,
            'iconModDate': datetime(2026, 11, 7)}


def state():
    return [[app('one'), app('two')], [
        {'elementType': 'widget', 'iconType': 'custom', 'iconLists': [], 'gridSize': 'medium',
         'bundleIdentifier': 'widget.extension', 'containerBundleIdentifier': 'one',
         'widgetIdentifier': 'weather', 'displayIdentifier': 'widget.instance',
         'userSelectedElementIdentifier': 'city.settings', 'futureField': b'\x00\xff'},
        {'listType': 'folder', 'displayName': 'Folder', 'iconLists': [[app('offloaded')]]},
        {'iconType': 'app', 'iconLists': [], 'bundleIdentifier': 'one', 'displayIdentifier': 'copy.instance'},
        {'webClipURL': 'https://example.com', 'displayIdentifier': 'web.instance'}]]


def inventory():
    return {'one': {'ApplicationType': 'User'}, 'two': {'ApplicationType': 'User'},
            'library': {'ApplicationType': 'User'},
            'offloaded': {'ApplicationType': 'User', 'IsPlaceholder': True},
            'hidden': {'ApplicationType': 'System', 'SBAppTags': ['hidden']}}


def plan():
    return create_plan(state(), inventory(), IDENTITY, METRICS,
                       [{'type': 'swap', 'page': 0, 'first': 0, 'second': 1}])


class FakePhone:
    """Import a wire archive into a simulated read model; no real device dependencies."""
    def __init__(self):
        self.state = state()
        self.apps_data = inventory()
        self.identity_data = dict(IDENTITY)
        self.sends = []
        self.closed = False
        self.fail_after_send = False
        self.disconnected_after_send = False
        self.ignore_send = False
        self.corrupt_readback = False
        self.drop_config_id = False
        self.opened = False

    async def devices(self):
        return [{'udid': UDID, 'connection_type': 'USB'}]

    async def open(self, udid):
        assert udid == UDID
        self.opened = True
        self.closed = False

    async def identity(self):
        if self.disconnected_after_send and self.sends:
            raise ConnectionError('Disconnected after send')
        return dict(self.identity_data)

    async def layout(self, format_version):
        return deepcopy(self.state)

    async def apps(self):
        return deepcopy(self.apps_data)

    async def metrics(self):
        return dict(METRICS)

    async def close(self):
        self.closed = True
        self.opened = False

    async def send_layout(self, payload):
        self.sends.append(deepcopy(payload))
        if not self.ignore_send:
            existing = {item_identifier(x): x for _, x, _ in walk(self.state) if item_identifier(x)}
            def decode(x):
                if isinstance(x, str):
                    if x not in existing:
                        raise AssertionError('Unknown reference in the payload')
                    return deepcopy(existing[x])
                result = deepcopy(x)
                if item_kind(result) == 'folder':
                    result['iconLists'] = [[decode(c) for c in p] for p in result['iconLists']]
                elif item_kind(result) in ('widget', 'widget_stack'):
                    result['iconLists'] = []
                    if result.get('uniqueIdentifier'):
                        result['userSelectedElementIdentifier'] = result.pop('uniqueIdentifier')
                    if self.drop_config_id:
                        result.pop('userSelectedElementIdentifier', None)
                return result
            self.state = [[decode(x) for x in payload['buttonBar']],
                          *[[decode(x) for x in p] for p in payload['iconLists']]]
            if self.corrupt_readback:
                self.state[0].reverse()
        if self.fail_after_send:
            raise ConnectionError('Send returned an error after the device imported the state')


class PlannerTests(unittest.TestCase):
    def test_plan_preserves_objects_and_reports_only_two_dock_changes(self):
        source = state()
        p = create_plan(source, inventory(), IDENTITY, METRICS,
                        [{'type': 'swap', 'page': 0, 'first': 0, 'second': 1}])
        self.assertEqual(source, state())
        self.assertEqual(p['target_state'][1:], source[1:])
        self.assertEqual(len(p['changes']), 2)
        self.assertEqual(p['payload']['ignored'], ['hidden', 'library'])
        widget = p['payload']['iconLists'][0][0]
        self.assertEqual(widget['uniqueIdentifier'], 'city.settings')
        self.assertEqual(widget['futureField'], b'\x00\xff')
        self.assertNotIn('iconLists', widget)
        self.assertEqual(p['payload']['iconLists'][0][2], 'copy.instance')
        self.assertEqual(p['payload']['iconLists'][0][3], 'web.instance')
        self.assertEqual(validate_plan(p), p)

    def test_source_and_plan_objects_cannot_be_smuggled_into_operations(self):
        for op in ({'type': 'replace', 'payload': {}},
                   {'type': 'swap', 'page': -1, 'first': 0, 'second': 1},
                   {'type': 'swap', 'page': 0, 'first': True, 'second': 1}):
            with self.subTest(op=op), self.assertRaises(ValueError):
                apply_operations(state(), [op])
        p = plan(); p['payload']['ignored'] = []
        with self.assertRaises(ValueError):
            validate_plan(p)
        p = plan(); p['target_state'][1].clear()
        with self.assertRaises(ValueError):
            validate_plan(p)

    def test_missing_inventory_unknown_type_and_duplicate_instances_are_refused(self):
        source = state()
        cases = [([], inventory()), (source, {}),
                 ([source[0], [False]], inventory()),
                 ([source[0], [app('one')]], inventory())]
        for source, apps in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                create_plan(source, apps, IDENTITY, METRICS,
                            [{'type': 'swap', 'page': 0, 'first': 0, 'second': 1}])
        apps = inventory(); del apps['offloaded']
        with self.assertRaises(ValueError):
            root_archive(state(), apps)

    def test_future_typed_objects_with_bundle_ids_are_not_flattened(self):
        for item in ({'bundleIdentifier': 'one', 'iconType': 'future'},
                     {'bundleIdentifier': 'one', 'elementType': 'future'},
                     {'bundleIdentifier': 'one', 'gridSize': 'medium'}):
            source = state(); source[1].append(item)
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_state(source, METRICS)

    def test_capacity_uses_widget_footprints(self):
        source = state()
        source[1] += [app('extra.' + str(i)) for i in range(20)]
        with self.assertRaises(ValueError):
            validate_state(source, METRICS)

    def test_readback_checks_entire_layout_config_aliases_and_unknown_widget_fields(self):
        expected = state(); actual = deepcopy(expected)
        widget = actual[1][0]
        widget['uniqueIdentifier'] = widget.pop('userSelectedElementIdentifier')
        self.assertTrue(compare_layout(expected, actual)['structure_matches'])
        self.assertFalse(compare_layout(expected, actual)['configuration_identity_conflict'])
        widget['uniqueIdentifier'] = 'different.city'
        self.assertTrue(compare_layout(expected, actual)['configuration_identity_conflict'])
        del widget['uniqueIdentifier']
        self.assertEqual(compare_layout(expected, actual)['configuration_identity'][0]['status'], 'not_returned')
        widget['futureField'] = b'changed'
        self.assertFalse(compare_layout(expected, actual)['structure_matches'])
        actual = deepcopy(expected); actual[1][1]['displayName'] = 'Renamed'
        self.assertFalse(compare_layout(expected, actual)['structure_matches'])

    def test_version_and_date_are_volatile_but_config_ids_are_strict_preconditions(self):
        a=state(); b=deepcopy(a)
        b[0][0]['iconModDate'] = datetime(2026, 11, 8)
        b[0][0]['bundleVersion'] = '2'
        self.assertEqual(state_fingerprint(a), state_fingerprint(b))
        b[1][0]['userSelectedElementIdentifier'] = 'different.city'
        self.assertNotEqual(state_fingerprint(a), state_fingerprint(b))

    def test_stacks_keep_selection_and_element_ids(self):
        source = state()
        source[1][0] = {'iconType': 'custom', 'gridSize': 'medium', 'displayIdentifier': 'stack',
                        'elements': [{'uniqueIdentifier': 'element', 'widgetIdentifier': 'weather'}],
                        'userSelectedElementIdentifier': 'element'}
        payload, transforms = root_archive(source, inventory())
        self.assertNotIn('uniqueIdentifier', payload['iconLists'][0][0])
        self.assertEqual(transforms, [])
        self.assertEqual(payload['iconLists'][0][0]['elements'][0]['uniqueIdentifier'], 'element')
        changed = deepcopy(source); changed[1][0]['elements'][0]['uniqueIdentifier'] = 'new'
        self.assertTrue(compare_layout(source, changed)['configuration_identity_conflict'])


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.phone = FakePhone()
        self.p = plan()
        self.token = snapshot_digest(self.p)
        self.sleeper = patch('layout_transaction.asyncio.sleep', new_callable=AsyncMock)
        self.sleeper.start()
        self.addCleanup(self.sleeper.stop)

    async def apply(self):
        return await execute(self.phone, self.p, self.token, self.root, timeout=1)

    async def test_apply_verify_and_restore_round_trip(self):
        path, receipt = await self.apply()
        self.assertEqual(receipt['status'], 'applied_structure_verified')
        self.assertEqual(self.phone.state[0], list(reversed(state()[0])))
        self.assertEqual(self.phone.state[1:], state()[1:])
        self.assertTrue(self.phone.closed)
        self.assertFalse(receipt['widget_contents_verified'])
        load_transaction(path)
        restored_path, restored = await execute(self.phone, self.p, self.token, self.root,
                                               timeout=1, restore_transaction=path)
        self.assertEqual(restored['status'], 'restored_structure_verified')
        self.assertEqual(self.phone.state, state())
        self.assertEqual(len(self.phone.sends), 2)
        saved = plistlib.loads((path / 'before-state.plist').read_bytes())
        self.assertEqual(saved, state())
        self.assertEqual(json.loads((restored_path / 'receipt.json').read_text())['status'], restored['status'])

    async def test_wrong_token_and_stale_plan_cannot_write(self):
        with self.assertRaises(ValueError):
            await execute(self.phone, self.p, 'bad-token', self.root)
        self.phone.state[0].reverse()
        _, receipt = await self.apply()
        self.assertEqual(receipt['status'], 'refused_before_write')
        self.assertEqual(self.phone.sends, [])

    async def test_wrong_device_and_changed_inventory_cannot_write(self):
        for change in ('device', 'inventory'):
            self.phone = FakePhone()
            if change == 'device':
                self.phone.identity_data['UniqueDeviceID'] = 'other'
            else:
                self.phone.apps_data['new.app'] = {'ApplicationType': 'User'}
            _, receipt = await self.apply()
            self.assertEqual(receipt['status'], 'refused_before_write')
            self.assertEqual(self.phone.sends, [])

    async def test_send_error_may_still_be_proven_by_readback_without_retry(self):
        self.phone.fail_after_send = True
        _, receipt = await self.apply()
        self.assertEqual(receipt['status'], 'applied_structure_verified')
        self.assertEqual(len(self.phone.sends), 1)
        self.assertTrue(any(e['name'] == 'send_error' for e in receipt['events']))

    async def test_disconnect_or_ignored_write_never_retries_or_blindly_restores(self):
        for mode in ('disconnected_after_send', 'ignore_send', 'corrupt_readback'):
            with self.subTest(mode=mode):
                self.phone = FakePhone()
                setattr(self.phone, mode, True)
                path, receipt = await self.apply()
                self.assertEqual(receipt['status'], 'write_outcome_uncertain')
                self.assertEqual(len(self.phone.sends), 1)
                with self.assertRaises(ValueError):
                    load_transaction(path)

    async def test_missing_configuration_identity_is_inconclusive_not_verified(self):
        self.phone.drop_config_id = True
        _, receipt = await self.apply()
        self.assertEqual(receipt['status'], 'applied_configuration_inconclusive')
        self.assertFalse(receipt['widget_contents_verified'])

    async def test_restore_refuses_changes_since_apply_and_corrupted_backup(self):
        path, _ = await self.apply()
        self.phone.state[1][1]['displayName'] = 'Edited on phone'
        _, receipt = await execute(self.phone, self.p, self.token, self.root,
                                   restore_transaction=path, timeout=1)
        self.assertEqual(receipt['status'], 'refused_before_write')
        self.assertEqual(len(self.phone.sends), 1)
        (path / 'before-state.plist').write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            load_transaction(path)

    async def test_backup_and_sending_receipt_exist_before_the_first_send(self):
        original = self.phone.send_layout
        async def checked(payload):
            dirs = [p for p in self.root.glob('apply-*') if p.is_dir()]
            self.assertEqual(len(dirs), 1)
            receipt = json.loads((dirs[0] / 'receipt.json').read_text())
            self.assertEqual(receipt['status'], 'sending')
            self.assertIn('before-state.plist', receipt['artifacts'])
            self.assertTrue((dirs[0] / 'before-state.plist').exists())
            await original(payload)
        self.phone.send_layout = checked
        _, receipt = await self.apply()
        self.assertEqual(receipt['status'], 'applied_structure_verified')

    async def test_real_cli_plan_review_then_trial_use_same_token_and_restore(self):
        capture, manifest = await collect(self.phone, UDID, self.root / 'captures')
        self.assertEqual(manifest['status'], 'complete')
        tool = Path(__file__).resolve().parents[1] / 'layout_tool.py'
        result = subprocess.run([sys.executable, str(tool), 'plan-swap', '--capture', str(capture),
                                 '--indexes', '0', '1', '--output', str(self.root / 'plan.plist')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        created = json.loads(result.stdout)
        reviewed = subprocess.run([sys.executable, str(tool), 'review', created['plan_file']],
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertEqual(json.loads(reviewed.stdout)['plan_token'], created['plan_token'])
        from layout_tool import read_plan
        p = read_plan(created['plan_file'])
        args = argparse.Namespace(command='trial', timeout=1, transaction_root=self.root / 'runs',
                                  plan_token=created['plan_token'])
        output = io.StringIO()
        with patch('layout_tool.UsbWriter', return_value=self.phone), redirect_stdout(output):
            code = await run_device(args, p)
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['apply_status'], 'applied_structure_verified')
        self.assertEqual(result['restore_status'], 'restored_structure_verified')
        self.assertEqual(self.phone.state, state())
        self.assertEqual(len(self.phone.sends), 2)

    async def test_corrupt_capture_is_refused_before_plan_creation(self):
        capture, _ = await collect(self.phone, UDID, self.root / 'captures')
        (capture / 'apps.plist').write_bytes(b'corrupt inventory')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            load_capture(capture)

    async def test_second_transaction_is_refused_while_device_lock_is_held(self):
        with device_lock(self.root, UDID):
            with self.assertRaisesRegex(ValueError, 'Another layout transaction'):
                await self.apply()
        self.assertEqual(self.phone.sends, [])

    async def test_cancel_after_send_records_uncertainty_without_another_write(self):
        started = asyncio.Event()
        blocked = asyncio.Event()
        original = self.phone.send_layout
        async def send_then_block(payload):
            await original(payload)
            started.set()
            await blocked.wait()
        self.phone.send_layout = send_then_block
        task = asyncio.create_task(execute(self.phone, self.p, self.token, self.root, timeout=30))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        receipts = list(self.root.glob('apply-*/receipt.json'))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0].read_text())['status'], 'write_outcome_uncertain')
        self.assertEqual(len(self.phone.sends), 1)
        self.assertTrue(self.phone.closed)

    async def test_usb_writer_sends_dictionary_once_and_does_not_wait_for_ack(self):
        service = type('FakeService', (), {})()
        service.connect = AsyncMock(); service.close = AsyncMock()
        service.service = type('Wire', (), {})()
        service.service.send_plist = AsyncMock()
        writer = UsbWriter(); writer.client = object()
        with patch('pymobiledevice3.services.springboard.SpringBoardServicesService', return_value=service):
            await writer.send_layout(self.p['payload'])
        service.service.send_plist.assert_awaited_once_with({'command': 'setIconState', 'iconState': self.p['payload']})
        service.close.assert_awaited_once()
        with self.assertRaises(ValueError):
            await writer.send_layout(state())


if __name__ == '__main__':
    unittest.main()
