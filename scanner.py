"""A scanner for symbolic dependencies between modules.

Each import copies a name in some other module to become a name in the
current scope; the "origin" of the import is the location of the original
name in package.module.name form.  Note that "a.b" could mean the name b
in the module a, or the module b in the package a; these two things are
indistinguishable from the referring module.
"""

import ast
import importlib
import os


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

def resolve_modpath(base, relpath, level=0):
    """Resolves the path of the module referred to by 'from ..x import y'."""
    if level == 0:
        return relpath
    parts = base.split('.') + ['_']
    parts = parts[:-level] + relpath.split('.')
    return '.'.join(parts)

def get_star_names(modpath):
    """Returns all the names imported by 'import *' from a given module."""
    module = importlib.import_module(modpath)
    if hasattr(module, '__all__'):
        return module.__all__
    return [name for name in dir(module) if not name.startswith('_')]


class OriginMap:
    """Collects a map, for each module, from names to their origins."""

    def __init__(self):
        self.origins = {}  # {modpath: {name: {origin, ...}}}

    def add(self, modpath, name, origin):
        """Adds a possible origin for the given name in the given module."""
        self.origins.setdefault(modpath, {}).setdefault(name, set()).add(origin)

    def add_package_origins(self, modpath):
        """Whenever you 'import a.b.c', Python automatically binds 'b' in a to
        the a.b module and binds 'c' in a.b to the a.b.c module."""
        parts = modpath.split('.')
        parent = parts[0]
        for part in parts[1:]:
            child = parent + '.' + part
            self.add(parent, part, child)  # TODO: skip if child isn't a module
            parent = child

    def scan_module(self, modpath, node):
        """Scans a module, collecting possible origins for all names, assuming
        names can only become bound to values in other modules by import."""

        def scan_imports(node):
            if node_type(node) == 'Import':
                for binding in node.names:
                    name, asname = binding.name, binding.asname
                    if asname:
                        self.add(modpath, asname, name)
                    else:
                        self.add(modpath, name, name.split('.')[0])
                    self.add_package_origins(name)

            elif node_type(node) == 'ImportFrom':
                frompath = resolve_modpath(modpath, node.module, node.level)
                for binding in node.names:
                    name, asname = binding.name, binding.asname
                    if name == '*':
                        for name in get_star_names(frompath):
                            self.add(modpath, name, frompath + '.' + name)
                        self.add_package_origins(frompath)
                    else:
                        self.add(modpath, asname or name, frompath + '.' + name)
                        self.add_package_origins(frompath + '.' + name)

            for_each_child(node, scan_imports)

        for_each_child(node, scan_imports)


def get_origins(root_path):
    """Scans some modules and collects an overall origin map."""
    import sys
    sys.path.append(root_path)

    origin_map = OriginMap()
    for dir_path, dir_names, file_names in os.walk(root_path):
        assert dir_path[:len(root_path)] == root_path
        subdir_path = dir_path[len(root_path):]
        package_parts = list(filter(lambda x: x, subdir_path.split('/')))
        for name in file_names:
            if name.endswith('.py'):
                modpath = '.'.join(package_parts + [name[:-3]])
                node = ast.parse(open(os.path.join(dir_path, name)).read())
                origin_map.scan_module(modpath, node)
    return origin_map.origins


if __name__ == '__main__':
    import sys
    import pprint
    pprint.pprint(get_origins(sys.argv[1]))
