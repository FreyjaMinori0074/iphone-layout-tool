"""Run the push wrapper with fake device/process commands; never contact a phone."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'push_layout.sh'


class PushValidationTests(unittest.TestCase):
    def run_isolated(self, data):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / 'calls'
            fake = root / 'cfgutil'
            fake.write_text('#!/bin/sh\necho cfgutil >> "$CALL_LOG"\n')
            fake.chmod(0o755)
            pgrep = root / 'pgrep'
            pgrep.write_text('#!/bin/sh\necho pgrep >> "$CALL_LOG"\nexit 1\n')
            pgrep.chmod(0o755)
            # Copy with the device command replaced; no production command is run.
            script = root / 'push.sh'
            script.write_text(SCRIPT.read_text().replace(
                'CFGUTIL="/Applications/Apple Configurator.app/Contents/MacOS/cfgutil"',
                f'CFGUTIL="{fake}"'))
            source = root / "layout's file.json"
            source.write_text(json.dumps(data))
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       CALL_LOG=str(marker))
            result = subprocess.run(['bash', str(script), str(source)], env=env,
                                    capture_output=True, text=True, timeout=10)
            return result, marker.read_text() if marker.exists() else ''

    def test_invalid_inputs_rejected_before_any_process_or_device_action(self):
        for data in ({'convertible': False, 'entries': []},
                     [[], [{'elementType': 'widget'}]],
                     [[], [['Folder']]], [[], [None]], [['a'] * 5, []]):
            with self.subTest(data=data):
                result, calls = self.run_isolated(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid cfgutil layout', result.stderr)
                self.assertEqual(calls, '')

    def test_supported_layout_with_quoted_filename_passes_validation(self):
        result, calls = self.run_isolated([
            ['dock.app'], ['app.one', ['Folder', ['app.two'], ['app.three']]]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 'pgrep\ncfgutil\n')


if __name__ == '__main__':
    unittest.main()
