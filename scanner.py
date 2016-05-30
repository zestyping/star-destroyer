"""A scanner for symbolic dependencies between modules.

Each import copies a name in some other module to become a name in the
current scope; the "origin" of the import is the location of the original
name in package.module.name form.  Note that "a.b" could mean the name b
in the module a, or the module b in the package a; these two things are
indistinguishable from the referring module.
"""

from __future__ import print_function
import ast
import importlib
import os
import sys


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
    if hasattr(importlib, 'find_loader'):
        loader = importlib.find_loader(modpath)
        if loader:
            return loader.path if hasattr(loader, 'path') else True
    else:
        relative_path = modpath.replace('.', '/') + '.py'
        for root_path in sys.path:
            path = os.path.join(root_path, relative_path)
            if os.path.isfile(path):
                return path


class ImportMap:
    """Collects a map, for each module, from imported names to their origins."""

    def __init__(self, find_module, import_module):
        self.map = {}  # {modpath: {name: {origin, ...}}}
        self.star_names = {}  # {modpath: [name, ...]}
        self.find_module = find_module
        self.import_module = import_module

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
                self.star_names[modpath] = getattr(
                    module, '__all__',
                    [name for name in dir(module) if not name.startswith('_')])
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


class NameResolver:
    """Resolves name lookups in modules, using an import map."""

    def __init__(self, import_map):
        self.import_map = import_map
        self.usage_map = {}

    def scan_module(self, modpath, node):
        """Scans a module, collecting all used origins, assuming that modules
        are obtained only by dotted paths and no other kinds of expressions."""

        used_origins = self.usage_map.setdefault(modpath, set())

        def get_origins(modpath, name):
            """Returns the chain of all origins for a given name in a module."""
            origins = set()
            for origin in self.import_map.get_origins(modpath, name):
                if origin not in origins:
                    origins.add(origin)
                    if '.' in origin:
                        origins.update(get_origins(*origin.rsplit('.', 1)))
            return origins

        def get_origins_for_node(node):
            """Returns the set of all possible origins to which the given
            dotted-path expression might dereference."""
            if node_type(node) == 'Name' and node_type(node.ctx) == 'Load':
                return {modpath + '.' + node.id} | get_origins(modpath, node.id)
            if node_type(node) == 'Attribute' and node_type(node.ctx) == 'Load':
                return set.union(set(), *(
                    {parent + '.' + node.attr} | get_origins(parent, node.attr)
                    for parent in get_origins_for_node(node.value)))
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
            else:
                for_each_child(node, scan_loads)

        for_each_child(node, scan_loads)

    def get_used_origins(self, modpath):
        return self.usage_map.get(modpath, set())


def get_modules(root_path):
    """Gets (pkgpath, modpath, ast) triples for all modules in a file tree."""
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
                yield (pkgpath, modpath, ast.parse(open(path).read()))


def scan(root_path):
    modules = list(get_modules(root_path))

    # Scan all the modules and collect a map of origins.
    sys.path.append(root_path)
    import_map = ImportMap(find_module, importlib.import_module)
    for (pkgpath, modpath, node) in modules:
        # print('Scanning: %s' % modpath, file=sys.stderr)
        import_map.scan_module(pkgpath, modpath, node)

    # Scan all the modules and look at all the names loaded.
    name_resolver = NameResolver(import_map)
    for (pkgpath, modpath, node) in modules:
        name_resolver.scan_module(modpath, node)

    return modules, import_map, name_resolver

def show_results(modules, import_map, name_resolver):
    print('\n=== NAME MAPPINGS ===')

    for (pkgpath, modpath, node) in modules:
        title = 'Names in %s' % modpath
        print('\n' + title + '\n' + '-'*len(title))
        for name, value in sorted(import_map.map.get(modpath, {}).items()):
            print('  %s -> %s' % (name, ', '.join(sorted(value))))

    print('\n=== ORIGINS USED ===')

    for (pkgpath, modpath, node) in modules:
        title = 'Used by %s' % modpath
        print('\n' + title + '\n' + '-'*len(title))
        for origin in sorted(name_resolver.get_used_origins(modpath)):
            print('  %s' % origin)

if __name__ == '__main__':
    if sys.argv[1] == '-t':
        import pickle
        modules, import_map, name_resolver = scan(sys.argv[2])
        with open(sys.argv[3], 'wb') as out:
            pickle.dump(import_map.map, out)
        with open(sys.argv[4], 'wb') as out:
            pickle.dump(name_resolver.usage_map, out)
    else:
        modules, import_map, name_resolver = scan(sys.argv[1])
        show_results(modules, import_map, name_resolver)
