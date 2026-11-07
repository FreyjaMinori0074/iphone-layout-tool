"""Experimental USB apply/restore transactions with durable evidence and no write retries."""
import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import tempfile

from capture_snapshot import UsbReader, _atomic_bytes, _json_view, canonical_udid, select_usb, validate_identity
from layout_plan import (compare_layout, inventory_view, root_archive, state_fingerprint,
                         validate_plan, validate_state)
from layout_snapshot import snapshot_bytes, snapshot_digest

DEFAULT_TRANSACTIONS = Path.home() / '.local/share/iphone-layout-tool/transactions'


class UsbWriter(UsbReader):
    """Separate from the read-only collector; never used by capture_snapshot.py."""
    async def send_layout(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {'buttonBar', 'iconLists', 'ignored'}
                or not all(isinstance(payload[k], list) for k in payload)
                or not all(isinstance(x, str) and x for x in payload['ignored'])):
            raise ValueError('Refusing a non-dictionary/invalid root archive')
        from pymobiledevice3.services.springboard import SpringBoardServicesService
        service = SpringBoardServicesService(lockdown=self.client)
        try:
            await service.connect()
            # A send/ack is not proof of import. Verification uses fresh service connections.
            await service.service.send_plist({'command': 'setIconState', 'iconState': payload})
        finally:
            try:
                await asyncio.wait_for(service.close(), timeout=3)
            except Exception:
                pass


@contextmanager
def device_lock(root, udid):
    locks = Path(root) / 'locks'
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(canonical_udid(udid).encode()).hexdigest() + '.lock'
    fd = os.open(locks / name, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another layout transaction for this device is running') from None
        yield
    finally:
        os.close(fd)


class TransactionStore:
    def __init__(self, root, mode):
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = Path(tempfile.mkdtemp(prefix=mode + '-', dir=root)).resolve()
        self.receipt = {'schema_version': 1, 'mode': mode, 'status': 'preparing', 'artifacts': {},
                        'events': [], 'write_attempted': False,
                        'widget_contents_verified': False, 'spatial_positions_verified': False,
                        'app_library_membership_verified': False}
        self.flush()

    def flush(self):
        _atomic_bytes(self.path / 'receipt.json',
                      (json.dumps(self.receipt, ensure_ascii=False, indent=2, default=_json_view) + '\n').encode())

    def event(self, name, **fields):
        self.receipt['events'].append({'name': name, **fields})
        self.flush()

    def save(self, name, value):
        data = snapshot_bytes(value)
        _atomic_bytes(self.path / name, data)
        self.receipt['artifacts'][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        self.flush()


def load_transaction(directory):
    directory = Path(directory)
    receipt = json.loads((directory / 'receipt.json').read_text())
    if receipt.get('schema_version') != 1 or receipt.get('mode') != 'apply':
        raise ValueError('Expected an apply transaction')
    result = {}
    # Reject tampering in every piece of evidence, including after-state and original backup.
    for name, metadata in receipt['artifacts'].items():
        if Path(name).name != name:
            raise ValueError('Invalid artifact path')
        data = (directory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata['sha256']:
            raise ValueError('Transaction checksum mismatch: ' + name)
        result[name] = plistlib.loads(data)
    if not receipt.get('write_attempted') or 'before-state.plist' not in result or 'plan.plist' not in result:
        raise ValueError('Transaction has no device-write backup')
    if receipt.get('status') not in ('applied_structure_verified', 'applied_configuration_inconclusive'):
        raise ValueError('Restore requires a structurally verified apply; inspect uncertain outcomes before making another write')
    if 'observed-state.plist' not in result:
        raise ValueError('Transaction has no verified post-write observation')
    validate_plan(result['plan.plist'])
    plan = result['plan.plist']
    if snapshot_digest(plan) != receipt.get('plan_token'):
        raise ValueError('Transaction plan token mismatch')
    if state_fingerprint(result['before-state.plist']) != plan['source_fingerprint']:
        raise ValueError('Backup is not the source state of the reviewed plan')
    if snapshot_digest(inventory_view(result['before-apps.plist'])) != plan['inventory_fingerprint']:
        raise ValueError('Backup inventory is inconsistent with the reviewed plan')
    check_device(result['before-identity.plist'], plan['device'])
    comparison = compare_layout(plan['target_state'], result['observed-state.plist'])
    if not comparison['structure_matches'] or comparison['configuration_identity_conflict']:
        raise ValueError('Recorded observation does not match the reviewed target')
    return receipt, result


async def _timed(operation, timeout):
    return await asyncio.wait_for(operation, timeout=timeout)


async def current_state(reader, udid, timeout):
    identity = await _timed(reader.identity(), timeout)
    validate_identity(identity, udid)
    state = await _timed(reader.layout('2'), timeout)
    apps = await _timed(reader.apps(), timeout)
    metrics = await _timed(reader.metrics(), timeout)
    validate_state(state, metrics)
    inventory_view(apps)
    last_state = await _timed(reader.layout('2'), timeout)
    last_identity = await _timed(reader.identity(), timeout)
    validate_identity(last_identity, udid)
    if any(identity.get(k) != last_identity.get(k) for k in ('UniqueDeviceID', 'ProductType', 'ProductVersion', 'BuildVersion')):
        raise ValueError('Identity changed while reading transaction preconditions')
    if state_fingerprint(state) != state_fingerprint(last_state):
        raise ValueError('Layout changed while reading transaction preconditions')
    return {'identity': identity, 'state': state, 'apps': apps, 'metrics': metrics}


def check_device(identity, planned):
    if any(identity.get(k) != planned.get(k) for k in ('ProductType', 'ProductVersion', 'BuildVersion')):
        raise ValueError('Device model or iOS build differs from the plan')
    if canonical_udid(identity.get('UniqueDeviceID')) != canonical_udid(planned.get('UniqueDeviceID')):
        raise ValueError('Device UDID differs from the plan')


async def execute(reader, plan, token, root=DEFAULT_TRANSACTIONS, timeout=20,
                  restore_transaction=None, progress=None):
    """Apply one reviewed plan, or restore its original backup after a verified apply.

    There is one send at most, even if the connection drops after sending. No exception
    handler writes a rollback. Any missing readback leaves an explicit uncertain outcome.
    """
    validate_plan(plan)
    if token != snapshot_digest(plan):
        raise ValueError('Plan token does not match the exact reviewed plan')
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
        raise ValueError('Invalid timeout')
    mode = 'restore' if restore_transaction is not None else 'apply'
    original = None
    if restore_transaction is not None:
        _, original = load_transaction(restore_transaction)
        if original['plan.plist'] != plan:
            raise ValueError('Restore transaction belongs to a different plan')
    udid = plan['device']['UniqueDeviceID']
    with device_lock(root, udid):
        store = TransactionStore(root, mode)
        r = store.receipt
        r.update({'plan_token': token, 'device': plan['device']})
        store.save('plan.plist', plan)
        try:
            if progress:
                progress('reading fresh device state')
            devices = await _timed(reader.devices(), timeout)
            selected = select_usb(devices, udid)
            await _timed(reader.open(selected['udid']), timeout)
            current = await current_state(reader, udid, timeout)
            check_device(current['identity'], plan['device'])
            if mode == 'apply':
                if state_fingerprint(current['state']) != plan['source_fingerprint']:
                    raise ValueError('Stale plan: phone layout changed; create a fresh plan')
                expected_inventory = plan['inventory_fingerprint']
                target = plan['target_state']
            else:
                target = original['before-state.plist']
                # Do not overwrite a layout the user has changed since this transaction.
                expected = original['observed-state.plist']
                if state_fingerprint(current['state']) == state_fingerprint(target):
                    r['status'] = 'already_at_original_state'
                    return store.path, r
                if state_fingerprint(current['state']) != state_fingerprint(expected):
                    raise ValueError('Phone changed after apply; refusing to overwrite subsequent edits')
                expected_inventory = snapshot_digest(inventory_view(original['before-apps.plist']))
            if snapshot_digest(inventory_view(current['apps'])) != expected_inventory:
                raise ValueError('App inventory changed; refresh the plan instead of guessing App Library state')
            validate_state(target, current['metrics'])
            if current['metrics'] != plan['metrics']:
                raise ValueError('Device metrics changed since planning')
            payload, transforms = root_archive(target, current['apps'])
            if mode == 'apply' and payload != plan['payload']:
                raise ValueError('Live payload differs from the reviewed payload')
            for filename, value in [('before-state.plist', current['state']), ('before-apps.plist', current['apps']),
                                    ('before-identity.plist', current['identity']), ('target-state.plist', target),
                                    ('payload.plist', payload)]:
                store.save(filename, value)
            r['field_transforms'] = transforms
            r['status'] = 'sending'
            r['write_attempted'] = True
            store.event('before_send', payload_sha256=snapshot_digest(payload))
            if progress:
                progress('sending layout once; backup is saved')
            try:
                await _timed(reader.send_layout(payload), timeout)
                store.event('send_returned')
            except Exception as exc:
                # The phone could have imported the data before the host saw the error.
                store.event('send_error', error_type=type(exc).__name__, error=str(exc))
            r['status'] = 'checking_readback'
            store.flush()
            last_comparison = None
            for attempt in range(3):
                if progress:
                    progress(f'checking full readback ({attempt + 1}/3)')
                try:
                    observed = await current_state(reader, udid, timeout)
                    check_device(observed['identity'], plan['device'])
                    store.save(f'observation-{attempt + 1}.plist', observed['state'])
                    if snapshot_digest(inventory_view(observed['apps'])) != expected_inventory:
                        raise ValueError('App inventory changed during write verification')
                    comparison = compare_layout(target, observed['state'])
                    last_comparison = comparison
                    store.event('readback', attempt=attempt + 1, **comparison)
                    if comparison['structure_matches'] and not comparison['configuration_identity_conflict']:
                        store.save('observed-state.plist', observed['state'])
                        r['comparison'] = comparison
                        if mode == 'restore':
                            r['status'] = 'restored_structure_verified'
                        else:
                            r['status'] = ('applied_configuration_inconclusive' if comparison['configuration_identity']
                                           else 'applied_structure_verified')
                        return store.path, r
                except Exception as exc:
                    store.event('readback_error', attempt=attempt + 1,
                                error_type=type(exc).__name__, error=str(exc))
                if attempt < 2:
                    await asyncio.sleep(1)
            r['status'] = 'write_outcome_uncertain'
            if last_comparison is not None:
                r['comparison'] = last_comparison
        except asyncio.CancelledError:
            r['status'] = 'write_outcome_uncertain' if r['write_attempted'] else 'cancelled_before_write'
            raise
        except Exception as exc:
            r['status'] = 'write_outcome_uncertain' if r['write_attempted'] else 'refused_before_write'
            r['failure'] = {'error_type': type(exc).__name__, 'error': str(exc)}
        finally:
            try:
                await asyncio.wait_for(reader.close(), timeout=min(timeout, 3))
            except Exception as exc:
                r['cleanup_warning'] = type(exc).__name__
            store.flush()
        return store.path, r
