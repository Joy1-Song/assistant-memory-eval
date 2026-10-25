"""Build the browser version of the blind spot-check (same 32 items as the Excel sheet).

python spotcheck/build_spotcheck_page.py  ->  spotcheck/spotcheck.html
Verdicts are saved to the artifact's db collection "verdicts" (doc id n01..n32); score_spotcheck.py can read
an exported JSON of those docs instead of the Excel sheet.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_spotcheck import sample_items  # noqa: E402

OUT = Path(__file__).resolve().parent / "spotcheck.html"
TEMPLATE = Path(__file__).resolve().parent / "spotcheck_template.html"


def main():
    items = sample_items()
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__ITEMS__", data), encoding="utf-8")
    print(f"{len(items)} items -> {OUT} (first: {items[0]['probe']} {items[0]['config']})")


if __name__ == "__main__":
    main()
