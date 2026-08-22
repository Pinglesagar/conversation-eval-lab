"""Entry point so the worked example regenerates itself.

    python -m lab.judges.hallucinated_confirmation

Exists as a separate module rather than an `if __name__ == "__main__"` block in
the package `__init__`, because `runpy` would otherwise import the package twice
and warn about it.
"""

from __future__ import annotations

import sys

from lab.judges.hallucinated_confirmation import main

if __name__ == "__main__":
    sys.exit(main())
