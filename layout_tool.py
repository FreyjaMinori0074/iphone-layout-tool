#!/usr/bin/env python3
"""Plan, review, apply and restore the first experimental full-layout chain.

Plan/review are offline. Apply/restore/trial contact only the explicitly bound USB
phone. Trial performs one swap and restores only after a structurally verified apply.
"""
import argparse
import asyncio
import json
from pathlib import Path
import plistlib
import sys

from capture_snapshot import _atomic_bytes, _json_view, positive_timeout
from layout_plan import create_plan, load_capture, validate_plan
from layout_snapshot import snapshot_bytes, snapshot_digest
from layout_transaction import DEFAULT_TRANSACTIONS, UsbWriter, execute, load_transaction

DEFAULT_PLANS = Path.home() / '.local/share/iphone-layout-tool/plans'
SUCCESS_APPLY = ('applied_structure_verified', 'applied_configuration_inconclusive')


def plan_review(plan):
    validate_plan(plan)
    return {'plan_token': snapshot_digest(plan), 'device': plan['device'],
            'changes': plan['changes'], 'field_transforms': plan['field_transforms'],
            'app_inventory_records': len(plan['inventory']),
            'ignored_records': len(plan['payload']['ignored']),
            'limitations': plan['limitations'], 'device_write_performed': False}


def read_plan(path):
    plan = plistlib.loads(Path(path).read_bytes())
    validate_plan(plan)
    return plan


async def run_device(args, plan):
    reader = UsbWriter(args.timeout)
    progress = lambda message: print(message, file=sys.stderr, flush=True)
    root = args.transaction_root
    if args.command == 'restore':
        path, receipt = await execute(reader, plan, args.plan_token, root, args.timeout,
                                       restore_transaction=args.transaction, progress=progress)
        print(json.dumps({'transaction': str(path), **receipt}, ensure_ascii=False, indent=2, default=_json_view))
        return 0 if receipt['status'] in ('restored_structure_verified', 'already_at_original_state') else 2
    path, receipt = await execute(reader, plan, args.plan_token, root, args.timeout, progress=progress)
    result = {'apply_transaction': str(path), 'apply_status': receipt['status']}
    if args.command == 'trial' and receipt['status'] in SUCCESS_APPLY:
        # Explicit trial command includes restoring the original layout; never retry a failed apply.
        restore_path, restored = await execute(UsbWriter(args.timeout), plan, args.plan_token, root, args.timeout,
                                               restore_transaction=path, progress=progress)
        result.update({'restore_transaction': str(restore_path), 'restore_status': restored['status']})
        code = 0 if restored['status'] in ('restored_structure_verified', 'already_at_original_state') else 2
    else:
        code = 0 if receipt['status'] in SUCCESS_APPLY else 2
    result['visual_positions_and_widget_contents_verified'] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('plan-swap', help='Offline: swap two references on the same page')
    make.add_argument('--capture', type=Path, required=True)
    make.add_argument('--page', type=int, default=0, help='0=Dock, 1=first Home Screen page')
    make.add_argument('--indexes', nargs=2, type=int, required=True)
    make.add_argument('--output', type=Path)
    review = sub.add_parser('review', help='Offline: review exact plan and token')
    review.add_argument('plan', type=Path)
    for command in ('apply', 'trial', 'restore'):
        p = sub.add_parser(command, help=('Write then restore the verified trial' if command == 'trial' else command))
        if command == 'restore':
            p.add_argument('--transaction', type=Path, required=True)
        else:
            p.add_argument('--plan', type=Path, required=True)
        p.add_argument('--plan-token', required=True, help='Exact token printed by offline review')
        p.add_argument('--timeout', type=positive_timeout, default=20)
        p.add_argument('--transaction-root', type=Path, default=DEFAULT_TRANSACTIONS)
    args = parser.parse_args()
    try:
        if args.command == 'plan-swap':
            capture = load_capture(args.capture)
            plan = create_plan(capture['source'], capture['apps'], capture['identity'], capture['metrics'],
                               [{'type': 'swap', 'page': args.page, 'first': args.indexes[0], 'second': args.indexes[1]}])
            path = args.output or DEFAULT_PLANS / (snapshot_digest(plan) + '.plist')
            if path.exists():
                raise ValueError('Output already exists; refusing to overwrite a reviewed plan')
            if path.suffix.lower() != '.plist':
                raise ValueError('Plans must be stored as binary .plist files')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _atomic_bytes(path, snapshot_bytes(plan))
            print(json.dumps({'plan_file': str(path.resolve()), **plan_review(plan)}, ensure_ascii=False, indent=2, default=_json_view))
            return 0
        if args.command == 'review':
            print(json.dumps(plan_review(read_plan(args.plan)), ensure_ascii=False, indent=2, default=_json_view))
            return 0
        if args.command == 'restore':
            _, artifacts = load_transaction(args.transaction)
            plan = artifacts['plan.plist']
        else:
            plan = read_plan(args.plan)
        return asyncio.run(run_device(args, plan))
    except KeyboardInterrupt:
        print('Interrupted; inspect the transaction receipt before any further device write.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException) as exc:
        print(f'Refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
