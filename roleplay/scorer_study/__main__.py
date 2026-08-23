"""Entry point so the study regenerates itself.

    python -m roleplay.scorer_study

A separate module rather than an `if __name__ == "__main__"` block inside the
package `__init__`, because `runpy` would otherwise import the package twice and
warn about it.
"""

from __future__ import annotations

import sys

from roleplay.scorer_study import main

if __name__ == "__main__":
    sys.exit(main())
