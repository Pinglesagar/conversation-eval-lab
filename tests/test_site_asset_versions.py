"""Every page's stylesheet and script links carry the content hash of the file they load.

GitHub Pages serves assets with a ten-minute cache. Without a version on the link, a
returning visitor gets the new HTML with the old CSS, and a section laid out by new
rules renders as unstyled text — which is exactly what happened in the local preview
before this test existed. The hash on the link must therefore move whenever the file
does, and this test is the only thing that makes forgetting impossible.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.html"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


@pytest.mark.parametrize(
    "pattern, asset, page",
    [
        (r'href="site/css/main\.css\?v=([0-9a-f]+)"', "docs/site/css/main.css", "docs/index.html"),
        (r'src="site/js/main\.js\?v=([0-9a-f]+)"', "docs/site/js/main.js", "docs/index.html"),
        (r'href="learn\.css\?v=([0-9a-f]+)"', "docs/learn/learn.css", "docs/learn/index.html"),
        (r'src="learn\.js\?v=([0-9a-f]+)"', "docs/learn/learn.js", "docs/learn/index.html"),
    ],
)
def test_asset_link_carries_the_current_content_hash(pattern: str, asset: str, page: str) -> None:
    html = (ROOT / page).read_text("utf-8")
    found = re.findall(pattern, html)
    assert len(found) == 1, f"expected exactly one versioned link matching {pattern!r}, found {len(found)}"
    expected = _digest(ROOT / asset)
    assert found[0] == expected, (
        f"{asset} changed but the link in {page} still says ?v={found[0]}; "
        f"set it to ?v={expected} in {page} (shasum -a 256 {asset} | cut -c1-10)"
    )
