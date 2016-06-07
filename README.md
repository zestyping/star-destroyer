## star\_destroyer: Eliminate `import *` from your modules

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
