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
        os.path.isfile(os.path.join(path, 'expected_imports')) and
        os.path.isfile(os.path.join(path, 'expected_usage'))):
        CASE_DIRS.append(path)

@pytest.mark.parametrize('path', CASE_DIRS)
def test_scanner(path):
    print('running: %s' % path)
    with tempfile.NamedTemporaryFile('rb') as imports_file:
        with tempfile.NamedTemporaryFile('rb') as usage_file:
            os.spawnl(os.P_WAIT, sys.executable, sys.executable, SCANNER_PATH,
                      '-t', path, imports_file.name, usage_file.name)
            actual_imports = pickle.load(imports_file)
            actual_usage = pickle.load(usage_file)

    with open(os.path.join(path, 'expected_imports')) as imports_file:
        expected_imports = json.load(imports_file)
    with open(os.path.join(path, 'expected_usage')) as usage_file:
        expected_usage = json.load(usage_file)

    for modpath in expected_imports:
        module_imports = actual_imports.setdefault(modpath, {})
        for name, value in module_imports.items():
            module_imports[name] = sorted(value)
    for modpath in expected_usage:
        actual_usage[modpath] = sorted(actual_usage.get(modpath, []))

    print('expected imports: %s' % json.dumps(expected_imports))
    print('  actual imports: %s' % json.dumps(actual_imports))

    print('expected usage: %s' % json.dumps(expected_usage))
    print('  actual usage: %s' % json.dumps(actual_usage))

    assert expected_imports == actual_imports
    assert expected_usage == actual_usage
    print('passed: %s' % path)
