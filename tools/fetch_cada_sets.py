#!/usr/bin/env python3
"""One-off fetch of the CaDA set list (set number, name, year, piece count) from Merlins Bricks into a LOCAL file.

Writes data/local/set-catalog-local.json, which is gitignored and must never be committed: merlinsbricks.com
states a copyright (Kai Zimmermann) and publishes no licence, so the list is local user data of whoever runs this
tool, on their own machine. Nothing fetched is bundled, and no network access happens anywhere else in the repo.

Polite by construction: one run is exactly three requests (robots.txt, the CaDA brand page, the JSON file that page
loads its table from), two seconds apart, with an identifying User-Agent. robots.txt is read first and the run
aborts if it disallows the paths. Run it once; do not schedule it. It refuses to overwrite an existing file unless
--force is given, so a re-run cannot hammer the site by accident.

The output is the optional local catalogue that the brand-aware catalogue merge (#123) reads next to
ldraw_tools/data/set-catalog.bin:

    {"format": "set-catalog-local/1", "source": ..., "fetched": "YYYY-MM-DD",
     "sets": [{"key": "cada:C55029W", "brand": "cada", "set_num": "C55029W", "name": ..., "year": 2024,
               "pieces": 242}, ...]}

"key" is the brand-prefixed lookup key (brand lower case, set number as published), "pieces" and "year" are null
when the site lists none (display boxes and frames have no piece count). EAN is not in the list data (it is only on
each set's own page) and is left out to keep the run to one pass.

Usage:
    python3 tools/fetch_cada_sets.py [--out FILE] [--force]
    python3 tools/fetch_cada_sets.py --from-json DATA.json [--out FILE] [--force]   # offline: parse a saved file
"""
import argparse
import json
import re
import sys
import time
import urllib.robotparser
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "data" / "local" / "set-catalog-local.json"
SITE = "https://www.merlinsbricks.com"
BRAND_PAGE = f"{SITE}/setdb/brand/cada/"
USER_AGENT = "open-brick-designer-fetch/1.0 (one-off personal fetch; https://github.com/AlessioNar/ldraw-nova)"
DELAY_S = 2.0
FORMAT = "set-catalog-local/1"
BRAND = "cada"

# Column order of the site's table rows: image, id, brand, name, rating, collections, favourites, brick type,
# pieces, release year, status. Only the ones below are used.
COL_SET_NUM, COL_NAME, COL_PIECES, COL_YEAR = 1, 3, 8, 9


def get(url):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as resp:
        return resp.read().decode("utf-8")


def data_file_url(brand_page_html):
    m = re.search(r'data-file=(\S+?)[\s>]', brand_page_html)
    if not m:
        raise ValueError("brand page has no data-file attribute; the site layout changed")
    return urljoin(SITE, m.group(1).strip('"\''))


def _int_or_none(v):
    return v if isinstance(v, int) else None


def parse_sets(data):
    """Rows of the site's table JSON -> catalogue entries, sorted by set number. Skips rows without a set number."""
    sets = {}
    for row in data["data"]:
        num = str(row[COL_SET_NUM]).strip()
        if not num:
            continue
        sets[num] = {
            "key": f"{BRAND}:{num}",
            "brand": BRAND,
            "set_num": num,
            "name": str(row[COL_NAME]).strip(),
            "year": _int_or_none(row[COL_YEAR]),
            "pieces": _int_or_none(row[COL_PIECES]),
        }
    return [sets[n] for n in sorted(sets)]


def document(sets, fetched):
    return {"format": FORMAT, "source": BRAND_PAGE, "fetched": fetched, "sets": sets}


def fetch_rows():
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(get(f"{SITE}/robots.txt").splitlines())
    if not rp.can_fetch(USER_AGENT, BRAND_PAGE):
        sys.exit("robots.txt disallows the CaDA brand page; not fetching")
    time.sleep(DELAY_S)
    page = get(BRAND_PAGE)
    url = data_file_url(page)
    if not rp.can_fetch(USER_AGENT, url):
        sys.exit("robots.txt disallows the data file; not fetching")
    time.sleep(DELAY_S)
    return json.loads(get(url))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output file (default {DEFAULT_OUT.relative_to(REPO)})")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output file")
    ap.add_argument("--from-json", type=Path, help="parse a saved data file instead of fetching (no network)")
    args = ap.parse_args()
    if args.out.exists() and not args.force:
        sys.exit(f"{args.out} exists; this is a one-off fetch, pass --force to run it again")
    data = json.loads(args.from_json.read_text(encoding="utf-8")) if args.from_json else fetch_rows()
    sets = parse_sets(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document(sets, date.today().isoformat()), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(sets)} CaDA sets to {args.out} (local data, gitignored; never commit it)")


if __name__ == "__main__":
    main()
