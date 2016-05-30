import json
import os
import pickle
import pprint
import pytest
import sys
import tempfile

CASES_PATH = 'cases'
SCANNER_PATH = os.path.join(os.path.dirname(__file__), 'scanner.py')

CASE_DIRS = []
for name in os.listdir(CASES_PATH):
    path = os.path.join(CASES_PATH, name)
    if (os.path.isdir(path) and
        os.path.isfile(os.path.join(path, 'expected_map')) and
        os.path.isfile(os.path.join(path, 'expected_usage'))):
        CASE_DIRS.append(path)

@pytest.mark.parametrize('path', CASE_DIRS)
def test_scanner(path):
    print('running: %s' % path)
    with tempfile.NamedTemporaryFile('rb') as map_file:
        with tempfile.NamedTemporaryFile('rb') as usage_file:
            os.spawnl(os.P_WAIT, sys.executable, sys.executable, SCANNER_PATH,
                      '-t', path, map_file.name, usage_file.name)
            actual_map = pickle.load(map_file)
            actual_usage = pickle.load(usage_file)

    with open(os.path.join(path, 'expected_map')) as map_file:
        expected_map = json.load(map_file)
    with open(os.path.join(path, 'expected_usage')) as usage_file:
        expected_usage = json.load(usage_file)

    for modpath in expected_map:
        module_map = actual_map.setdefault(modpath, {})
        for name, value in module_map.items():
            module_map[name] = sorted(value)
    for modpath in expected_usage:
        actual_usage[modpath] = sorted(actual_usage.get(modpath, []))

    print('expected map: %s' % json.dumps(expected_map))
    print('  actual map: %s' % json.dumps(actual_map))

    print('expected usage: %s' % json.dumps(expected_usage))
    print('  actual usage: %s' % json.dumps(actual_usage))

    assert expected_map == actual_map
    assert expected_usage == actual_usage
    print('passed: %s' % path)
