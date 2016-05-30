# Copyright 2016 Ka-Ping Yee.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy
# of the License at: http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distrib-
# uted under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, either express or implied.  See the License for
# specific language governing permissions and limitations under the License.

"""Eliminates 'import *' from your modules.

To run this over your code, provide the root path to your files, like so:

    python -m star_destroyer /path/to/files

This would scan any Python files at /path/to/files/*.py.  If you want to
process a package, provide the path to the directory containing the package,
not the package directory itself.  The path you provide should be a path as
it would appear in sys.path.  PYTHONPATH should also be set as it would be
set during a normal run of your code; it will be used to find other modules
that your code imports.

Running with just a path will print out the results of the scan and the edits
that would be made, without actually performing the edits.  To actually edit
your files, run star_destroyer with the -e option, like so:

    python -m star_destroyer -e /path/to/files

To run the tests, execute 'py.test' using Python 2.7 or Python 3.
"""

from __future__ import print_function
import ast
import importlib
import os
import sys

__version__ = '1.0'

def node_type(node):
    """Returns the name of an AST node's class."""
    if isinstance(node, ast.AST):
        return node.__class__.__name__

def for_each_child(node, callback):
    """Calls the callback for each AST node that's a child of the given node."""
    for name in node._fields:
        value = getattr(node, name)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    callback(item)
        elif isinstance(value, ast.AST):
            callback(value)

def resolve_frompath(pkgpath, relpath, level=0):
    """Resolves the path of the module referred to by 'from ..x import y'."""
    if level == 0:
        return relpath
    parts = pkgpath.split('.') + ['_']
    parts = parts[:-level] + (relpath.split('.') if relpath else [])
    return '.'.join(parts)

def find_module(modpath):
    """Determines whether a module exists with the given modpath."""
    module_path = modpath.replace('.', '/') + '.py'
    init_path = modpath.replace('.', '/') + '/__init__.py'
    for root_path in sys.path:
        path = os.path.join(root_path, module_path)
        if os.path.isfile(path):
            return path
        path = os.path.join(root_path, init_path)
        if os.path.isfile(path):
            return path


class ImportMap:
    """Collects a map, for each module, from imported names to their origins."""

    # Each import copies a name in some other module to become a name in the
    # current scope; the "origin" of the import is the location of the original
    # name in package.module.name form.  Note that "a.b" could mean the name b
    # in the module a, or the module b in the package a; these two things are
    # indistinguishable from the referring module.

    def __init__(self, find_module, import_module):
        self.map = {}  # {modpath: {name: {origin, ...}}}
        self.star_names = {}  # {modpath: [name, ...]}
        self.find_module = find_module
        self.import_module = import_module

    def __repr__(self):
        return '<ImportMap>'

    def get_star_names(self, modpath):
        """Returns all the names imported by 'import *' from a given module."""
        if modpath not in self.star_names:
            print('Importing %s to resolve import *' % modpath, file=sys.stderr)
            try:
                module = self.import_module(modpath)
            except ImportError:
                print('ERROR: Failed to import %s!' % modpath, file=sys.stderr)
                self.star_names[modpath] = []
            else:
                self.star_names[modpath] = sorted(getattr(
                    module, '__all__',
                    [name for name in dir(module) if not name.startswith('_')]))
        return self.star_names[modpath]

    def add(self, modpath, name, origin):
        """Adds a possible origin for the given name in the given module."""
        self.map.setdefault(modpath, {}).setdefault(name, set()).add(origin)

    def add_package_origins(self, modpath):
        """Whenever you 'import a.b.c', Python automatically binds 'b' in a to
        the a.b module and binds 'c' in a.b to the a.b.c module."""
        parts = modpath.split('.')
        parent = parts[0]
        for part in parts[1:]:
            child = parent + '.' + part
            if self.find_module(child):
                self.add(parent, part, child)
            parent = child

    def scan_module(self, pkgpath, modpath, node):
        """Scans a module, collecting possible origins for all names, assuming
        names can only become bound to values in other modules by import."""

        def scan_imports(node):
            if node_type(node) == 'Import':
                for binding in node.names:
                    name, asname = binding.name, binding.asname
                    if asname:
                        self.add(modpath, asname, name)
                    else:
                        top_name = name.split('.')[0]
                        self.add(modpath, top_name, top_name)
                    self.add_package_origins(name)

            elif node_type(node) == 'ImportFrom':
                frompath = resolve_frompath(pkgpath, node.module, node.level)
                for binding in node.names:
                    name, asname = binding.name, binding.asname
                    if name == '*':
                        for name in self.get_star_names(frompath):
                            self.add(modpath, name, frompath + '.' + name)
                        self.add_package_origins(frompath)
                    else:
                        self.add(modpath, asname or name, frompath + '.' + name)
                        self.add_package_origins(frompath + '.' + name)

            else:
                for_each_child(node, scan_imports)

        for_each_child(node, scan_imports)

    def get_origins(self, modpath, name):
        """Returns the set of possible origins for a name in a module."""
        return self.map.get(modpath, {}).get(name, set())

    def dump(self):
        """Prints out the contents of the import map."""
        for modpath in sorted(self.map):
            title = 'Imports in %s' % modpath
            print('\n' + title + '\n' + '-'*len(title))
            for name, value in sorted(self.map.get(modpath, {}).items()):
                print('  %s -> %s' % (name, ', '.join(sorted(value))))


class UsageMap:
    """Resolves name lookups in modules, using an import map."""

    def __init__(self, import_map):
        self.import_map = import_map
        self.map = {}

    def __repr__(self):
        return '<UsageMap>'

    def scan_module(self, modpath, node):
        """Scans a module, collecting all used origins, assuming that modules
        are obtained only by dotted paths and no other kinds of expressions."""

        used_origins = self.map.setdefault(modpath, set())

        def get_origins(modpath, name):
            """Returns the chain of all origins for a given name in a module."""
            origins = set()

            def walk_origins(modpath, name):
                for origin in self.import_map.get_origins(modpath, name):
                    if origin not in origins:
                        origins.add(origin)
                        if '.' in origin:
                            walk_origins(*origin.rsplit('.', 1))

            walk_origins(modpath, name)
            return origins

        def get_origins_for_node(node):
            """Returns the set of all possible origins to which the given
            dotted-path expression might dereference."""
            if node_type(node) == 'Name' and node_type(node.ctx) == 'Load':
                return {modpath + '.' + node.id} | get_origins(modpath, node.id)
            if node_type(node) == 'Attribute' and node_type(node.ctx) == 'Load':
                return set.union(set(), *[
                    {parent + '.' + node.attr} | get_origins(parent, node.attr)
                    for parent in get_origins_for_node(node.value)])
            return set()

        def get_origins_used_by_node(node):
            """Returns the set of all possible origins that could be used
            during dereferencing of the given dotted-path expression."""
            if node_type(node) == 'Name':
                return get_origins_for_node(node)
            if node_type(node) == 'Attribute':
                return set.union(get_origins_used_by_node(node.value),
                                 get_origins_for_node(node))
            return set()

        def scan_loads(node):
            if node_type(node) in ['Name', 'Attribute']:
                used_origins.update(get_origins_used_by_node(node))
            for_each_child(node, scan_loads)

        for_each_child(node, scan_loads)

        intermediate_origins = set()
        for origin in used_origins:
            parts = origin.split('.')
            for i in range(1, len(parts)):
                intermediate_origins.add('.'.join(parts[:i]))
        used_origins.update(intermediate_origins)

    def get_used_origins(self, modpath):
        return self.map.get(modpath, set())

    def get_modpaths(self):
        return self.map.keys()

    def dump(self):
        """Prints out the contents of the usage map."""
        for modpath in sorted(self.map):
            title = 'Used by %s' % modpath
            print('\n' + title + '\n' + '-'*len(title))
            for origin in sorted(self.get_used_origins(modpath)):
                print('  %s' % origin)


class StarDestroyer:
    def __init__(self, import_map, usage_map):
        self.import_map = import_map
        self.usage_map = usage_map
        self.all_used = set.union(*(usage_map.get_used_origins(modpath)
                                    for modpath in usage_map.get_modpaths()))

    def edit_module(self, pkgpath, modpath, path, node, actually_write=False):
        lines = open(path).readlines()
        original_lines = lines[:]

        import_stars = []

        def find_import_stars(node):
            if node_type(node) == 'ImportFrom':
                for binding in node.names:
                    if binding.name == '*':
                        import_stars.append(node)
            else:
                for_each_child(node, find_import_stars)

        for_each_child(node, find_import_stars)

        if import_stars:
            print('\n--- %s ---' % path, file=sys.stderr)

        for node in import_stars:
            frompath = resolve_frompath(pkgpath, node.module, node.level)
            ln = node.lineno - 1
            start = node.col_offset

            orig = original_lines[ln]
            end = orig.index('*') + 1
            names = [name for name in self.import_map.get_star_names(frompath)
                     if modpath + '.' + name in self.all_used]
            imp = ('from %s import %s' %
                   ('.'*node.level + node.module, ', '.join(names))
                   if names else '')
            print('%s  ==>  %s' % (orig[start:end], imp or '(deleted)'),
                  file=sys.stderr)
            lines[ln] = (orig[:start] + imp + orig[end:]).rstrip()
            lines[ln] += '\n' if lines[ln] else ''

        if lines != original_lines:
            if actually_write:
                with open(path, 'w') as file:
                    file.write(''.join(lines))
            return True


def get_modules(root_path):
    """Gets (pkgpath, modpath, path, ast) for all modules in a file tree."""
    for dir_path, dir_names, file_names in os.walk(root_path):
        assert dir_path[:len(root_path)] == root_path
        subdir_path = dir_path[len(root_path):]
        package_parts = list(filter(lambda x: x, subdir_path.split('/')))
        for name in file_names:
            if name.endswith('.py'):
                path = os.path.join(dir_path, name)
                pkgpath = '.'.join(package_parts)
                modpath = (pkgpath if name == '__init__.py' else
                           '.'.join(package_parts + [name[:-3]]))
                yield (pkgpath, modpath, path, ast.parse(open(path).read()))

def scan(root_path):
    modules = list(get_modules(root_path))

    # Scan all the modules and collect a map of origins.
    sys.path.append(root_path)
    import_map = ImportMap(find_module, importlib.import_module)
    for (pkgpath, modpath, path, node) in modules:
        # print('Scanning: %s' % modpath, file=sys.stderr)
        import_map.scan_module(pkgpath, modpath, node)

    # Scan all the modules and look at all the names loaded.
    usage_map = UsageMap(import_map)
    for (pkgpath, modpath, path, node) in modules:
        usage_map.scan_module(modpath, node)

    return modules, import_map, usage_map

def edit(modules, import_map, usage_map, actually_write=False):
    # Finally, edit the 'import *' lines in all the modules.
    star_destroyer = StarDestroyer(import_map, usage_map)
    for (pkgpath, modpath, path, node) in modules:
        if star_destroyer.edit_module(
            pkgpath, modpath, path, node, actually_write):
            if actually_write:
                print('Edited %s' % path, file=sys.stderr)


def show_results(modules, import_map, usage_map):
    print('\n=== IMPORT MAPPINGS ===')
    import_map.dump()

    print('\n=== ORIGINS USED ===')
    usage_map.dump()

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or '-h' in args or '--help' in args:
        print(__doc__)

    elif '-t' in args:
        args.pop(args.index('-t'))
        [root_path, import_map_path, usage_map_path] = args

        import pickle
        modules, import_map, usage_map = scan(root_path)
        with open(import_map_path, 'wb') as out:
            pickle.dump(import_map.map, out)
        with open(usage_map_path, 'wb') as out:
            pickle.dump(usage_map.map, out)

    elif '-e' in args:
        args.pop(args.index('-e'))
        [root_path] = args
        modules, import_map, usage_map = scan(root_path)
        edit(modules, import_map, usage_map, actually_write=True)

    else:
        [root_path] = args
        modules, import_map, usage_map = scan(root_path)
        show_results(modules, import_map, usage_map)
        edit(modules, import_map, usage_map, actually_write=False)
