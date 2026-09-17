"""Allow ``python -m loxmtec``."""

import sys

from loxmtec.main import main

if __name__ == "__main__":
    sys.exit(main())
