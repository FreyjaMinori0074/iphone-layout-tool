import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from convert_layout import LayoutError, classify, convert_layout, inspect_layout

SCRIPT = Path(__file__).resolve().parents[1] / 'convert_layout.py'


def app(identifier):
    return {'bundleIdentifier': identifier, 'displayName': identifier}


def widget():
    return {'elementType': 'widget', 'iconType': 'custom', 'iconLists': [],
            'gridSize': 'medium', 'displayIdentifier': 'widget-instance',
            'bundleIdentifier': 'example.extension', 'widgetIdentifier': 'example.widget'}


class ConversionTests(unittest.TestCase):
    def test_supported_apps_webclips_and_folder_pages(self):
        single = {'listType': 'folder', 'displayName': '购物',
                  'iconLists': [[app('shop.one')]]}
        multi = {'displayName': 'Tools',
                 'iconLists': [[app('tool.one')], [app('tool.two')]]}
        raw = [[app('dock.one')], [single, multi, {'webClipURL': 'https://example.com'},
                                  {'displayIdentifier': 'app.display-only'}]]
        self.assertEqual(convert_layout(raw), [['dock.one'], [
            ['购物', 'shop.one'], ['Tools', ['tool.one'], ['tool.two']],
            'https://example.com', 'app.display-only']])

    def test_widget_and_special_app_are_not_folders(self):
        special = {'iconType': 'app', 'iconLists': [],
                   'bundleIdentifier': 'example.app', 'displayIdentifier': 'instance-id'}
        self.assertEqual(classify(widget()), 'widget')
        self.assertEqual(classify(special), 'special_app')
        with self.assertRaises(LayoutError) as caught:
            convert_layout([[], [widget(), special]])
        self.assertEqual(len(caught.exception.issues), 2)
        self.assertIn('layout[1][0]', caught.exception.issues[0])
        self.assertIn('layout[1][1]', caught.exception.issues[1])

    def test_folder_children_are_checked_instead_of_silently_filtered(self):
        raw = [[], [{'listType': 'folder', 'displayName': 'Folder',
                     'iconLists': [[app('one'), widget(), None]]}]]
        with self.assertRaises(LayoutError) as caught:
            convert_layout(raw)
        self.assertEqual(len(caught.exception.issues), 2)
        self.assertIn('iconLists[0][1]', str(caught.exception))
        self.assertIn('iconLists[0][2]', str(caught.exception))

    def test_malformed_layouts_and_empty_folders_refused(self):
        for raw in ({}, [], [[]], [[], {}], [[], [None]], [[], [{}]],
                    [['a'] * 5, []], [[], [False]],
                    [[], [{'listType': 'folder', 'displayName': 'Empty', 'iconLists': []}]],
                    [[], [{'listType': 'folder', 'displayName': 'Empty', 'iconLists': [[]]}]],
                    [[], [{'iconLists': [], 'bundleIdentifier': 'mystery'}]]):
            with self.subTest(raw=raw), self.assertRaises(LayoutError):
                convert_layout(raw)

    def test_unknown_typed_objects_not_flattened_to_apps(self):
        for item in ({'elementType': 'future-widget', 'bundleIdentifier': 'one'},
                     {'iconType': 'future-type', 'bundleIdentifier': 'one'},
                     {'gridSize': 'large', 'bundleIdentifier': 'one'}):
            with self.subTest(item=item), self.assertRaises(LayoutError):
                convert_layout([[], [item]])

    def test_inspection_preserves_original_fields(self):
        w = widget()
        w['futureConfiguration'] = {'arbitrary': [1, 2, 3]}
        raw = [[], [w]]
        original = copy.deepcopy(raw)
        report = inspect_layout(raw)
        self.assertFalse(report['convertible'])
        self.assertEqual(report['counts'], {'widget': 1})
        self.assertEqual(report['entries'][0]['displayIdentifier'], 'widget-instance')
        self.assertEqual(raw, original)


class CliTests(unittest.TestCase):
    def test_failure_leaves_existing_output_untouched_and_stdout_empty(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.json'
            output = Path(td) / 'existing.json'
            source.write_text(json.dumps([[], [widget()]]))
            output.write_text('existing contents')
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), '-o', str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertIn('layout[1][0]', result.stderr)
            self.assertEqual(output.read_text(), 'existing contents')

    def test_inspect_reports_without_conversion(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.json'
            source.write_text(json.dumps([[], [widget()]]))
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--inspect'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['convertible'])

    def test_source_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source.json'
            original = json.dumps([[], [app('one')]])
            source.write_text(original)
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), '-o', str(source)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(source.read_text(), original)


if __name__ == '__main__':
    unittest.main()
