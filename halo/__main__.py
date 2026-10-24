# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Entry point: python3 -m halo [PATH] | --shred PATH... | --version | --help"""

import sys

USAGE = """Usage: halo [PATH]
       halo --shred PATH...
       halo --version | --help

  PATH            open Halo and scan this folder or drive
  --shred PATH…   open the shred confirmation sheet for these items
                  (nothing is ever shredded without the sheet)
  --version       print the version and exit
  --help          show this help and exit

Halo works entirely offline. Set HALO_DRY_RUN=1 to run every shred step
without writing, renaming or deleting anything.
"""


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv[1:]:
        from .paths import read_version
        print("Halo %s" % read_version())
        return 0
    if "--help" in argv[1:] or "-h" in argv[1:]:
        print(USAGE)
        return 0
    for a in argv[1:]:
        if a.startswith("-") and a not in ("--shred",):
            sys.stderr.write("halo: unknown option %s\n\n%s" % (a, USAGE))
            return 2
    from .app import HaloApplication
    app = HaloApplication()
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
