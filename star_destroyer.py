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

"""Eliminates `import *` from your modules.

To run this over your code, provide the root path to your files, like so:

    python -m star_destroyer /path/to/files

This would scan all `*.py` files anywhere under `/path/to/files`.  The path
you provide should be a path as it would appear in `sys.path` -- thus, if you
want to process a package, provide the path to the parent of the package
directory, not the package directory itself.

Running with just a path will print out the results of the scan and the edits
that would be made, without actually performing the edits.  If you want
`star_destroyer` to actually edit your files to replace all the `import *`
statements, run it with the `-e` option, like so:

    python -m star_destroyer -e /path/to/files

`star_destroyer` has been tested with Python 2.7 and Python 3.5.  Run it using
the same version of Python that your code is written for.  `PYTHONPATH` should
also be set as it would be set during a normal run of your code; it will be
used to find other modules that your code imports.

To run the tests, execute `py.test` using Python 2.7 or Python 3.5.
"""

from __future__ import print_function
import argparse
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


def node_to_text(node):
    """Transforms nodes to a valid python source.

    This is only supported for nodes with the type 'ImportFrom', 'Import', 'Name', 'Attribute', or 'alias'.
    Additionally, NoneType input will return the empty string.
    """
    if node is None:
        return ""
    elif node_type(node) == 'ImportFrom':
        return "from {} import {}".format('.' * node.level + (node.module if node.module is not None else ""),
                                          ', '.join(node_to_text(name) for name in node.names))
    elif node_type(node) == 'Import':
        return "import {}".format(', '.join(node_to_text(name) for name in node.names))
    elif node_type(node) == 'Name':
        return node.id
    elif node_type(node) == 'Attribute':
        return "{}.{}".format(node_to_text(node.value), node.attr)
    elif node_type(node) == 'alias':
        return "{} as {}".format(node.name, node.asname) if node.asname is not None else node.name


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
            print('\n' + title + '\n' + '-' * len(title))
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
            print('\n' + title + '\n' + '-' * len(title))
            for origin in sorted(self.get_used_origins(modpath)):
                print('  %s' % origin)


class StarImportCollector(ast.NodeVisitor):

    def __init__(self):
        self.star_imports = []

    def visit_ImportFrom(self, node):
        if len(node.names) == 1 and node.names[0].name == '*':
            self.star_imports.append(node)


class BaseStarDestroyer(ast.NodeVisitor):
    """Base class for different methods of removing star-imports.

    After running visit on a module :attr:`changes` will contain a list of (old_node, new_node)-tuples which, when
    applied transform the AST into a version without star-imports.
    """

    def __init__(self, import_map, all_used, pkgpath, modpath, star_imports):
        """Creates an instance.

        :param import_map: An :class:`ImportMap` containing the modules imports.
        :param all_used: A set of names used in the module.
        :param pkgpath: The path to the file that defines the module.
        :param modpath: The qualified name of the module.
        """
        self._star_provided_names = {star_import: [name for name
                                                   in import_map.get_star_names(resolve_frompath(
                                                       pkgpath, star_import.module, star_import.level))
                                                   if modpath + '.' + name in all_used]
                                     for star_import in star_imports}
        self._provided_names = set.union(*list(set(names) for names in self._star_provided_names.values()))
        self.changes = []


class QualifyStarDestroyer(BaseStarDestroyer):
    """A :class:`BaseStarDestroyer` that replaces usages of star-imported names with an access to the module."""

    def __init__(self, import_map, all_used, pkgpath, modpath, star_imports, module_aliases=dict()):
        """Creates an instance.

        :param module_aliases: A dictionary aliases for modules to use. Example: {'numpy', 'np'}
        See also: :method:`BaseStarDestroyer.__init__`
        """
        BaseStarDestroyer.__init__(self, import_map, all_used, pkgpath, modpath, star_imports)
        self._module_aliases = module_aliases

    def visit_ImportFrom(self, node):
        new_node = node
        if node in self._star_provided_names:
            names = self._star_provided_names[node]
            if len(names) == 0:
                new_node = node
            elif node.level == 0:

                new_node = ast.Import(names=[ast.alias(name=node.module,
                                                       asname=self._module_aliases.get(
                                                           node.module.split('.')[-1]))])
            else:
                modules = [module for module in node.module.split('.') if module != '']
                module = '.' * node.level + '.'.join(modules[:-1]) if len(modules) > 1 else None
                name = modules[-1]
                new_node = ast.ImportFrom(module=module,
                                          names=[ast.alias(name=name, asname=self._module_aliases.get(name))],
                                          level=node.level)

        if node != new_node:
            if new_node is not None:
                ast.copy_location(new_node, node)
            self.changes.append((node, new_node))

    def visit_Name(self, node):
        new_node = node
        if node.id in self._provided_names:
            providing_import = next((star_import for star_import, provided_names in
                                     self._star_provided_names.items() if node.id in provided_names), None)
            if providing_import is None:
                return  # Should not happen, error handling?

            new_providing_name = self._module_aliases.get(providing_import.module.split('.')[-1],
                                                          providing_import.module.split('.')[-1])
            new_node = ast.Attribute(value=ast.Name(id=new_providing_name, ctx=ast.Load()), attr=node.id, ctx=node.ctx)

        if node != new_node:
            if new_node is not None:
                ast.copy_location(new_node, node)
            self.changes.append((node, new_node))


class ReplaceStarDestroyer(BaseStarDestroyer):
    """A :class:`BaseStarDestroyer` that adds all uses elements names to the corresponding star-imports."""

    def __init__(self, import_map, all_used, pkgpath, modpath, star_imports):
        """Creates an instance.

        See also: :method:`BaseStarDestroyer.__init__`
        """
        BaseStarDestroyer.__init__(self, import_map, all_used, pkgpath, modpath, star_imports)

    def visit_ImportFrom(self, node):
        new_node = node
        if node in self._star_provided_names:
            names = self._star_provided_names[node]
            if len(names) == 0:
                new_node = node
            else:
                new_node = ast.ImportFrom(module=node.module,
                                          names=[ast.alias(name=name, asname=None) for name in names],
                                          level=node.level)

        if node != new_node:
            if new_node is not None:
                ast.copy_location(new_node, node)
            self.changes.append((node, new_node))


class StarDestroyer:
    def __init__(self, import_map, usage_map):
        self.import_map = import_map
        self.usage_map = usage_map
        self.all_used = set.union(*(usage_map.get_used_origins(modpath)
                                    for modpath in usage_map.get_modpaths()))

    def edit_module(self, pkgpath, modpath, path, node, method, dry_run=True, **kwd):
        ast.fix_missing_locations(node)

        star_import_collector = StarImportCollector()
        star_import_collector.visit(node)
        star_imports = star_import_collector.star_imports

        if len(star_imports) > 0:
            print('\n--- %s ---' % path, file=sys.stderr)

            if method == 'replace':
                star_destroyer = ReplaceStarDestroyer(self.import_map, self.all_used,
                                                      pkgpath, modpath, star_imports)
            elif method == 'qualify':
                star_destroyer = QualifyStarDestroyer(self.import_map, self.all_used,
                                                      pkgpath, modpath, star_imports, **kwd)
            else:
                return False

            star_destroyer.visit(node)
            if len(star_destroyer.changes) > 0:
                with open(path, 'r+') as module_file:
                    lines = module_file.readlines()
                    original_lines = lines[:]
                    change_log = []
                    for change in star_destroyer.changes:
                        line = change[0].lineno - 1
                        col = change[0].col_offset
                        if node_to_text(change[0]) not in lines[line][col:]:
                            print("Could not find change: {} to {} location in line {}:{}"
                                  .format(node_to_text(change[0]), node_to_text(change[1]),
                                          line + 1, col))
                            print(lines[line])
                            print(lines[line + 1])
                            break
                        else:
                            """lines[line] = (lines[line][:change[0].col_offset] +
                                           lines[line][change[0].col_offset].replace(
                                               node_to_text(change[0]), node_to_text(change[1])))"""
                            lines[line] = (lines[line][:change[0].col_offset] +
                                           lines[line][change[0].col_offset:].replace(
                                               node_to_text(change[0]), node_to_text(change[1])))
                            change_log.append("Change \"{}\" to \"{}\" location in line {}"
                                              .format(node_to_text(change[0]), node_to_text(change[1]), line + 1))
                    else:
                        print('\n'.join(change_log))
                        if not dry_run and any(l1 == l2 for l1, l2 in zip(lines, original_lines)):
                            module_file.seek(0)
                            module_file.write(''.join(lines))
                            module_file.truncate()

                        return True
                    return False


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
                try:
                    node = ast.parse(open(path).read())
                except SyntaxError:
                    print('ERROR: Invalid syntax in %s' % path, file=sys.stderr)
                else:
                    yield (pkgpath, modpath, path, node)


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


def edit(modules, import_map, usage_map, method, dry_run=True, **kwd):
    # Finally, edit the 'import *' lines in all the modules.
    print(dry_run)
    star_destroyer = StarDestroyer(import_map, usage_map)
    for (pkgpath, modpath, path, node) in modules:
        if star_destroyer.edit_module(
                pkgpath, modpath, path, node, method, dry_run, **kwd):
            if not dry_run:
                print('Edited %s' % path, file=sys.stderr)


def show_results(modules, import_map, usage_map):
    print('\n=== IMPORT MAPPINGS ===')
    import_map.dump()

    print('\n=== ORIGINS USED ===')
    usage_map.dump()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-c', '--command', choices=('print', 'apply', 'dump'), default='print',
                        help="The action to take: 'print' will compute all changes, but only"
                             "print them. 'apply' will actually change the files and 'dump'"
                             "will write the import- and usage-maps to disk for later use.")
    parser.add_argument('-m', '--method', choices=('replace', 'qualify'), default='replace',
                        help="Chose the behavior to use for removing star-imports. Replace will"
                             "replace the star with a list of all usages from the module (and"
                             "remove the import if that list would be empty). 'qualify' changes"
                             "the code to import the module and qualify its members in the module"
                             "by its name.")
    parser.add_argument('-r', '--replacements', action='append', type=lambda kv: kv.split('='),
                        help="A list ofkey=value pairs specifying module names and their"
                             "replacements. For example: '-r numpy=np -r Tkinter=tk'")
    parser.add_argument('root_path', help="The path to run the script on.")
    parser.add_argument('import_map_path', nargs='?', help="Destination for writing the import-map.")
    parser.add_argument('usage_map_path', nargs='?', help="Destination for writing the usage-map.")
    args = parser.parse_args()

    module_aliases = {k: v for k, v in args.replacements}

    if args.command == 'dump':
        import pickle
        modules, import_map, usage_map = scan(args.root_path)
        with open(args.import_map_path, 'wb') as out:
            pickle.dump(import_map.map, out)
        with open(args.usage_map_path, 'wb') as out:
            pickle.dump(usage_map.map, out)
    elif args.command == 'apply':
        modules, import_map, usage_map = scan(args.root_path)
        edit(modules, import_map, usage_map, method=args.method, dry_run=False, module_aliases=module_aliases)
    elif args.command == 'print':
        modules, import_map, usage_map = scan(args.root_path)
        show_results(modules, import_map, usage_map)
        edit(modules, import_map, usage_map, method=args.method, dry_run=True, module_aliases=module_aliases)
