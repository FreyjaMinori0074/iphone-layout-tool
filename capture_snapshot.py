#!/usr/bin/env python3
"""Read-only USB snapshot collector. No layout-write, restore or pairing commands.

Run doctor offline, list devices via usbmuxd, then capture an explicit trusted UDID.
Native responses are saved as binary plist; the JSON manifest is an index, not a backup.
"""
import argparse
import base64
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from layout_snapshot import LayoutSnapshot, snapshot_bytes, snapshot_digest

DEFAULT_ROOT = Path.home() / '.local/share/iphone-layout-tool/snapshots'
IDENTITY_KEYS = ('UniqueDeviceID', 'ProductType', 'ProductVersion', 'BuildVersion', 'DeviceName')


def canonical_udid(value):
    return value.replace('-', '').lower() if isinstance(value, str) else ''


def select_usb(devices, udid):
    matches = [x for x in devices if x['connection_type'] == 'USB'
               and canonical_udid(x['udid']) == canonical_udid(udid)]
    if len(matches) != 1:
        raise ValueError('Expected exactly one USB connection for the requested UDID. Connect and unlock that phone.')
    return matches[0]


def doctor():
    """Import installed APIs and inspect signatures; never create a connection."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    from pymobiledevice3.services.springboard import SpringBoardServicesService
    from pymobiledevice3.usbmux import list_devices
    checks = {
        'usb_target_and_no_autopair': {'serial', 'autopair', 'connection_type'} <= set(inspect.signature(create_using_usbmux).parameters),
        'placeholder_inventory': 'show_placeholders' in inspect.signature(InstallationProxyService.get_apps).parameters,
        'format_versions': 'format_version' in inspect.signature(SpringBoardServicesService.get_icon_state).parameters,
        'grid_metrics': callable(getattr(SpringBoardServicesService, 'get_homescreen_icon_metrics', None)),
        'usb_discovery': inspect.iscoroutinefunction(list_devices),
    }
    return {'pymobiledevice3': version('pymobiledevice3'), 'checks': checks, 'ready': all(checks.values())}


class UsbReader:
    """The entire device-facing surface: discovery, identity, layout, inventory, metrics."""
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.client = None

    async def devices(self):
        from pymobiledevice3.usbmux import list_devices
        return [{'udid': d.serial, 'connection_type': d.connection_type} for d in await list_devices()]

    async def open(self, udid):
        from pymobiledevice3.lockdown import create_using_usbmux
        self.client = await create_using_usbmux(serial=udid, connection_type='USB', autopair=False)
        mux_device = self.client.service.mux_device
        if (mux_device is None or mux_device.connection_type != 'USB'
                or canonical_udid(mux_device.serial) != canonical_udid(udid)):
            raise ValueError('Connected transport or device differs from the requested USB device')
        if not self.client.paired:
            raise ValueError('Existing trust pairing is unavailable. Unlock the phone and establish trust in Finder; capture will not create a pairing.')

    async def identity(self):
        result = {}
        for key in IDENTITY_KEYS:
            value = await self.client.get_value(key=key)
            if value is not None:
                result[key] = value
        return result

    async def _service_read(self, service_class, operation):
        service = service_class(lockdown=self.client)
        try:
            await service.connect()
            return await operation(service)
        finally:
            try:
                await asyncio.wait_for(service.close(), timeout=min(self.timeout, 3))
            except Exception:
                pass  # The operation result/error, not cleanup, defines the capture step.

    async def layout(self, format_version):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        return await self._service_read(SpringBoardServicesService,
                                        lambda s: s.get_icon_state(format_version=format_version))

    async def apps(self):
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        return await self._service_read(InstallationProxyService, lambda s: s.get_apps(
            application_type='Any', calculate_sizes=False, show_placeholders=True))

    async def metrics(self):
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        return await self._service_read(SpringBoardServicesService, lambda s: s.get_homescreen_icon_metrics())

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None


def _json_view(value):
    # Human-readable metadata only. Native values remain in the plist artifacts.
    if isinstance(value, datetime):
        return {'type': 'datetime', 'iso8601': value.isoformat()}
    if isinstance(value, bytes):
        return {'type': 'bytes', 'base64': base64.b64encode(value).decode('ascii')}
    raise TypeError(f'Unsupported metadata type: {type(value).__name__}')


def _atomic_bytes(path, data):
    """Restrict files on the internal disk and publish them only after a complete write."""
    fd, temp_name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


class CaptureStore:
    def __init__(self, root):
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        prefix = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_')
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=root)).resolve()
        try:
            dependency_version = version('pymobiledevice3')
        except PackageNotFoundError:
            dependency_version = 'not_installed'
        self.manifest = {'schema_version': 1, 'status': 'in_progress',
                         'software': {'python': sys.version.split()[0], 'pymobiledevice3': dependency_version},
                         'started_at': datetime.now(timezone.utc).isoformat(),
                         'read_only': True, 'transport': 'USB', 'steps': {}, 'artifacts': {},
                         'ready_for_device_write': False,
                         'limitations': ['A research snapshot is not a full iPhone backup.',
                                         'Page positions and widget settings have not been verified.']}
        self.flush()

    def flush(self):
        _atomic_bytes(self.path / 'manifest.json',
                      (json.dumps(self.manifest, ensure_ascii=False, indent=2, default=_json_view) + '\n').encode())

    def save(self, name, value):
        data = snapshot_bytes(value)
        _atomic_bytes(self.path / name, data)
        self.manifest['artifacts'][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        self.flush()


def validate_identity(identity, udid):
    if not isinstance(identity, dict) or canonical_udid(identity.get('UniqueDeviceID')) != canonical_udid(udid):
        raise ValueError('Device identity does not match the explicitly selected UDID')
    for key in ('ProductType', 'ProductVersion', 'BuildVersion'):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise ValueError(f'Device identity is missing {key}')
    return identity


def validate_inventory(apps):
    if not isinstance(apps, dict) or not apps:
        raise ValueError('App inventory is missing or empty')
    if any(not isinstance(k, str) or not k or not isinstance(v, dict) for k, v in apps.items()):
        raise ValueError('App inventory has an unrecognized structure')
    return apps


async def collect(reader, udid, root=DEFAULT_ROOT, timeout=20, progress=None):
    """No retries and no writes to the device, including on errors or cancellation."""
    if not isinstance(udid, str) or not udid.strip():
        raise ValueError('An explicit UDID is required')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be a positive finite number')
    store = CaptureStore(root)
    manifest = store.manifest
    manifest['requested_udid'] = udid

    async def step(name, operation, filename=None, validator=None, required=True):
        if progress:
            progress(name)
        try:
            result = await asyncio.wait_for(operation(), timeout=timeout)
            # Save the raw response before interpretation, including unsupported optional formats.
            if filename:
                store.save(filename, result)
            if validator:
                validator(result)
            manifest['steps'][name] = {'status': 'ok'}
            store.flush()
            return result
        except Exception as exc:
            message = 'operation timed out' if isinstance(exc, TimeoutError) else str(exc)
            manifest['steps'][name] = {'status': 'failed', 'error_type': type(exc).__name__, 'error': message}
            store.flush()
            if required:
                raise
            return None

    try:
        devices = await step('usb_discovery', reader.devices)
        selected = select_usb(devices, udid)
        await step('connect_existing_pairing', lambda: reader.open(selected['udid']))
        before = await step('identity_before', reader.identity, 'identity-before.plist',
                            lambda x: validate_identity(x, udid))
        state = await step('layout_v2_before', lambda: reader.layout('2'), 'layout-v2.plist', LayoutSnapshot.from_raw)
        apps = await step('apps_including_placeholders', reader.apps, 'apps.plist', validate_inventory)
        manifest['app_inventory'] = {'entry_count': len(apps),
                                     'requested_application_type': 'Any',
                                     'requested_show_placeholders': True,
                                     'app_library_membership_verified': False}
        await step('layout_v3_reference', lambda: reader.layout('3'), 'layout-v3.plist', LayoutSnapshot.from_raw, required=False)
        await step('grid_metrics', reader.metrics, 'metrics.plist',
                   lambda x: _validate_metrics(x), required=False)
        after_state = await step('layout_v2_after', lambda: reader.layout('2'), 'layout-v2-after.plist', LayoutSnapshot.from_raw)
        after = await step('identity_after', reader.identity, 'identity-after.plist',
                           lambda x: validate_identity(x, udid))
        # A second read is evidence of stability, not an atomic transaction or a position check.
        identity_stable = all(before.get(k) == after.get(k) for k in IDENTITY_KEYS if k != 'DeviceName')
        raw_stable = snapshot_digest(state) == snapshot_digest(after_state)
        manifest['observed_identity_stable'] = identity_stable
        manifest['observed_layout_response_equal'] = raw_stable
        manifest['layout_summary'] = LayoutSnapshot.from_raw(state).describe()
        if not identity_stable or not raw_stable:
            manifest['status'] = 'changed_during_capture'
        else:
            manifest['status'] = 'complete'
    except asyncio.CancelledError:
        manifest['status'] = 'interrupted'
        raise
    except Exception as exc:
        manifest['status'] = 'partial'
        manifest['failure'] = {'error_type': type(exc).__name__, 'error': str(exc)}
    finally:
        try:
            await asyncio.wait_for(reader.close(), timeout=min(timeout, 3))
        except Exception as exc:
            manifest['cleanup_warning'] = type(exc).__name__
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        store.flush()
    return store.path, manifest


def _validate_metrics(value):
    if not isinstance(value, dict) or not value or 'Error' in value:
        raise ValueError('Grid metrics unavailable or unrecognized')


def positive_timeout(value):
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 120:
        raise argparse.ArgumentTypeError('timeout must be greater than 0 and at most 120 seconds')
    return result


async def run(args):
    if args.command == 'doctor':
        result = doctor()
        print(json.dumps(result, indent=2))
        return 0 if result['ready'] else 2
    reader = UsbReader(args.timeout)
    if args.command == 'devices':
        devices = await asyncio.wait_for(reader.devices(), timeout=args.timeout)
        print(json.dumps([d for d in devices if d['connection_type'] == 'USB'], indent=2))
        return 0
    path, manifest = await collect(reader, args.udid, args.output_root, args.timeout,
                                   progress=lambda name: print(f'Reading: {name}', file=sys.stderr, flush=True))
    print(json.dumps({'capture_directory': str(path), 'status': manifest['status'],
                      'steps': manifest['steps'], 'ready_for_device_write': False}, ensure_ascii=False, indent=2))
    return 0 if manifest['status'] == 'complete' else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Check installed APIs offline; no device access')
    devices = sub.add_parser('devices', help='List USB connections from the local usbmux daemon')
    devices.add_argument('--timeout', type=positive_timeout, default=20)
    capture = sub.add_parser('capture', help='Read the specified trusted USB device; never write a layout')
    capture.add_argument('--udid', required=True)
    capture.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    capture.add_argument('--timeout', type=positive_timeout, default=20, help='Per-operation timeout in seconds')
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print('Interrupted. Any partial snapshot remains in the output root.', file=sys.stderr)
        return 130
    except (ImportError, OSError, ValueError, TimeoutError) as exc:
        print(f'Capture unavailable: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
