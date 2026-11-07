import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from capture_snapshot import collect, doctor, select_usb, UsbReader
from layout_snapshot import LayoutSnapshot, item_identifier

UDID = '00008150-EXAMPLE'
IDENTITY = {'UniqueDeviceID': UDID, 'ProductType': 'iPhone18,2',
            'ProductVersion': '27.2', 'BuildVersion': '24B119'}


def sample():
    return [[{'bundleIdentifier': 'dock', 'displayIdentifier': 'dock'}], [
        {'elementType': 'widget', 'iconType': 'custom', 'gridSize': 'medium',
         'bundleIdentifier': 'widget.extension', 'displayIdentifier': 'widget.instance',
         'userSelectedElementIdentifier': 'settings.instance', 'iconLists': [],
         'futureBlob': b'\x00\xff', 'futureDate': datetime(2026, 11, 7, 4, 30)},
        {'listType': 'folder', 'displayName': 'Folder', 'iconLists': [[
            {'displayIdentifier': 'offloaded.app', 'displayName': 'Placeholder'}]]},
        {'iconType': 'app', 'iconLists': [], 'displayIdentifier': 'special.instance',
         'bundleIdentifier': 'dock'}]]


class FakeReader:
    """Only the allowed read operations exist. Unexpected calls fail the test."""
    def __init__(self):
        self.calls = []
        self.connected = False
        self.state = sample()
        self.layout_calls = 0
        self.fail = None
        self.optional_bad = False
        self.changed = False
        self.identity_mismatch = False
        self.identity_calls = 0

    async def devices(self):
        self.calls.append('devices')
        return [{'udid': UDID, 'connection_type': 'Network'},
                {'udid': UDID, 'connection_type': 'USB'}]

    async def open(self, udid):
        self.calls.append(('open', udid))
        self.connected = True

    async def identity(self):
        self.calls.append('identity')
        self.identity_calls += 1
        result = dict(IDENTITY)
        if self.identity_mismatch and self.identity_calls == 2:
            result['UniqueDeviceID'] = 'another-phone'
        return result

    async def layout(self, format_version):
        self.calls.append(('layout', format_version))
        if self.fail == 'layout':
            raise ConnectionError('Cable disconnected')
        if format_version == '3' and self.optional_bad:
            return {'Error': 'UnsupportedFormatVersion'}
        result = deepcopy(self.state)
        if format_version == '2':
            self.layout_calls += 1
            if self.changed and self.layout_calls == 2:
                result[1].append({'bundleIdentifier': 'new.app'})
        return result

    async def apps(self):
        self.calls.append('apps')
        if self.fail == 'apps':
            raise ConnectionError('Cable disconnected during inventory')
        if self.fail == 'timeout':
            await asyncio.sleep(10)
        return {'dock': {'ApplicationType': 'User'},
                'library.only': {'ApplicationType': 'User'},
                'offloaded.app': {'ApplicationType': 'User', 'IsPlaceholder': True},
                'system.hidden': {'ApplicationType': 'System', 'SBAppTags': ['hidden']}}

    async def metrics(self):
        self.calls.append('metrics')
        return {'homeScreenIconColumns': 4, 'homeScreenIconRows': 6}

    async def close(self):
        self.calls.append('close')
        self.connected = False


class SnapshotModelTests(unittest.TestCase):
    def test_native_types_and_unknown_fields_survive_roundtrip_and_copy(self):
        raw = sample()
        model = LayoutSnapshot.from_raw(raw)
        raw[1][0]['futureBlob'] = b'changed'
        copy = model.raw_copy()
        copy[1].clear()
        restored = plistlib.loads(model.to_bytes())
        self.assertEqual(restored, sample())
        self.assertIsInstance(restored[1][0]['futureDate'], datetime)
        self.assertEqual(restored[1][0]['futureBlob'], b'\x00\xff')

    def test_instances_do_not_collide_by_parent_bundle(self):
        report = LayoutSnapshot.from_raw(sample()).describe()
        self.assertEqual(report['counts'], {'app': 2, 'widget': 1, 'folder': 1, 'special_app': 1})
        self.assertEqual(report['instances'][0]['identifier'], 'widget.instance')
        self.assertEqual(item_identifier({'displayIdentifier': 'copy', 'bundleIdentifier': 'app'}), 'copy')
        self.assertFalse(report['widget_settings_verified'])
        self.assertFalse(report['spatial_positions_verified'])

    def test_stack_elements_retained_without_inventing_configuration_ids(self):
        raw = [[], [{'iconType': 'custom', 'elements': [
            {'uniqueIdentifier': 'element.one', 'future': b'\x01'}],
            'userSelectedElementIdentifier': 'element.one'}]]
        model = LayoutSnapshot.from_raw(raw)
        self.assertEqual(model.raw_copy(), raw)
        self.assertNotIn('uniqueIdentifier', model.raw_copy()[1][0])
        self.assertEqual(model.describe()['instances'][0]['element_count'], 1)

    def test_dictionary_root_keeps_metadata_and_unknown_fields(self):
        raw = {'buttonBar': [], 'iconLists': [[]], 'ignored': ['library.app'],
               'listMetadata': {'page.one': {'fixedLocations': {'app': 5}}},
               'future': b'bytes'}
        self.assertEqual(plistlib.loads(LayoutSnapshot.from_raw(raw).to_bytes()), raw)

    def test_error_response_is_not_a_layout(self):
        with self.assertRaises(ValueError):
            LayoutSnapshot.from_raw({'Error': 'UnsupportedFormatVersion'})


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, reader, timeout=1):
        # Keep all generated private files in a per-test temporary directory.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path, manifest = await collect(reader, UDID, td.name, timeout=timeout)
        self.assertEqual(json.loads((path / 'manifest.json').read_text())['status'], manifest['status'])
        return path, manifest

    async def test_complete_capture_is_lossless_hashed_and_strictly_read_only(self):
        reader = FakeReader()
        path, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(plistlib.loads((path / 'layout-v2.plist').read_bytes()), sample())
        apps = plistlib.loads((path / 'apps.plist').read_bytes())
        self.assertTrue(apps['offloaded.app']['IsPlaceholder'])
        self.assertEqual(apps['system.hidden']['SBAppTags'], ['hidden'])
        for name, meta in manifest['artifacts'].items():
            contents = (path / name).read_bytes()
            self.assertEqual(hashlib.sha256(contents).hexdigest(), meta['sha256'])
        self.assertEqual(reader.calls, ['devices', ('open', UDID), 'identity', ('layout', '2'),
            'apps', ('layout', '3'), 'metrics', ('layout', '2'), 'identity', 'close'])
        self.assertFalse(reader.connected)
        self.assertFalse(manifest['ready_for_device_write'])
        self.assertTrue(manifest['observed_layout_response_equal'])
        self.assertEqual((path.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((path / 'layout-v2.plist').stat().st_mode & 0o777), 0o600)

    async def test_native_values_in_summary_do_not_destroy_manifest(self):
        reader = FakeReader()
        reader.state[1][0]['uniqueIdentifier'] = b'future-id'
        _, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['app_inventory']['entry_count'], 4)

    async def test_unavailable_or_network_only_target_never_opens(self):
        for discovered in ([], [{'udid': UDID, 'connection_type': 'Network'}],
                           [{'udid': 'other', 'connection_type': 'USB'}]):
            with self.subTest(devices=discovered):
                reader = FakeReader()
                reader.devices = AsyncMock(return_value=discovered)
                _, manifest = await self.capture(reader)
                self.assertEqual(manifest['status'], 'partial')
                self.assertEqual(reader.calls, ['close'])

    async def test_disconnect_retains_partial_snapshot_and_never_retries(self):
        reader = FakeReader()
        reader.fail = 'apps'
        path, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'partial')
        self.assertTrue((path / 'layout-v2.plist').exists())
        self.assertFalse((path / 'apps.plist').exists())
        self.assertEqual(reader.calls.count('apps'), 1)
        self.assertFalse(reader.connected)

    async def test_timeout_is_recorded_and_connection_closed(self):
        reader = FakeReader()
        reader.fail = 'timeout'
        _, manifest = await self.capture(reader, timeout=0.02)
        self.assertEqual(manifest['status'], 'partial')
        self.assertEqual(manifest['steps']['apps_including_placeholders']['error_type'], 'TimeoutError')
        self.assertFalse(reader.connected)

    async def test_unsupported_optional_format_is_saved_and_reported(self):
        reader = FakeReader()
        reader.optional_bad = True
        path, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['steps']['layout_v3_reference']['status'], 'failed')
        self.assertEqual(plistlib.loads((path / 'layout-v3.plist').read_bytes()),
                         {'Error': 'UnsupportedFormatVersion'})
        self.assertFalse(manifest['ready_for_device_write'])

    async def test_changed_layout_cannot_be_reported_as_stable(self):
        reader = FakeReader()
        reader.changed = True
        _, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'changed_during_capture')
        self.assertFalse(manifest['observed_layout_response_equal'])

    async def test_device_identity_mismatch_leaves_partial_evidence(self):
        reader = FakeReader()
        reader.identity_mismatch = True
        path, manifest = await self.capture(reader)
        self.assertEqual(manifest['status'], 'partial')
        self.assertEqual(manifest['steps']['identity_after']['status'], 'failed')
        self.assertTrue((path / 'identity-after.plist').exists())

    async def test_cancellation_preserves_interrupted_manifest_and_closes(self):
        reader = FakeReader()
        reader.fail = 'timeout'
        with tempfile.TemporaryDirectory() as td:
            task = asyncio.create_task(collect(reader, UDID, td, timeout=30))
            # Wait for the fake reader's real outstanding inventory operation.
            for _ in range(100):
                if 'apps' in reader.calls:
                    break
                await asyncio.sleep(0.001)
            self.assertIn('apps', reader.calls)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            manifests = list(Path(td).glob('*/manifest.json'))
            self.assertEqual(len(manifests), 1)
            self.assertEqual(json.loads(manifests[0].read_text())['status'], 'interrupted')
            self.assertFalse(reader.connected)

    async def test_new_capture_does_not_overwrite_previous(self):
        with tempfile.TemporaryDirectory() as td:
            first, _ = await collect(FakeReader(), UDID, td)
            second, _ = await collect(FakeReader(), UDID, td)
            self.assertNotEqual(first, second)
            self.assertTrue((first / 'manifest.json').exists())


class InstalledApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_selects_usb_disables_autopair_and_never_calls_write(self):
        client = SimpleNamespace(service=SimpleNamespace(mux_device=SimpleNamespace(
            connection_type='USB', serial=UDID)), paired=True, close=AsyncMock())
        with patch('pymobiledevice3.lockdown.create_using_usbmux', new_callable=AsyncMock) as create:
            create.return_value = client
            reader = UsbReader()
            await reader.open(UDID)
            create.assert_awaited_once_with(serial=UDID, connection_type='USB', autopair=False)
            await reader.close()
            client.close.assert_awaited_once()

    async def test_reader_refuses_network_fallback_and_unpaired_devices(self):
        for transport, paired in [('Network', True), ('USB', False)]:
            client = SimpleNamespace(service=SimpleNamespace(mux_device=SimpleNamespace(
                connection_type=transport, serial=UDID)), paired=paired, close=AsyncMock())
            with patch('pymobiledevice3.lockdown.create_using_usbmux', new_callable=AsyncMock) as create:
                create.return_value = client
                reader = UsbReader()
                with self.assertRaises(ValueError):
                    await reader.open(UDID)
                await reader.close()

    async def test_device_methods_only_use_expected_read_queries(self):
        reader = UsbReader()
        reader.client = SimpleNamespace(get_value=AsyncMock(side_effect=lambda key: IDENTITY.get(key)))
        self.assertEqual(await reader.identity(), IDENTITY)
        sb = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(),
                             get_icon_state=AsyncMock(return_value=sample()),
                             get_homescreen_icon_metrics=AsyncMock(return_value={'rows': 6}))
        ip = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), get_apps=AsyncMock(return_value={}))
        with patch('pymobiledevice3.services.springboard.SpringBoardServicesService', return_value=sb), \
             patch('pymobiledevice3.services.installation_proxy.InstallationProxyService', return_value=ip):
            await reader.layout('2')
            await reader.metrics()
            await reader.apps()
        sb.get_icon_state.assert_awaited_once_with(format_version='2')
        sb.get_homescreen_icon_metrics.assert_awaited_once_with()
        ip.get_apps.assert_awaited_once_with(application_type='Any', calculate_sizes=False, show_placeholders=True)
        self.assertEqual(sb.close.await_count, 2)
        ip.close.assert_awaited_once()

    async def test_offline_doctor_does_not_discover_or_connect(self):
        with patch('pymobiledevice3.usbmux.list_devices', new_callable=AsyncMock) as discover, \
             patch('pymobiledevice3.lockdown.create_using_usbmux', new_callable=AsyncMock) as connect:
            # Mocking the signatures changes doctor results, but must never cause I/O.
            doctor()
            discover.assert_not_awaited()
            connect.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
