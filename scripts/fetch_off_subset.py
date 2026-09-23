"""Fetch a small, dated snapshot of sports-nutrition products from Open Food Facts.

Data: Open Food Facts (https://openfoodfacts.org), ODbL 1.0 / DbCL 1.0.
The snapshot is written to data/raw/ (gitignored); this script is what we commit.

Usage: python3 scripts/fetch_off_subset.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://world.openfoodfacts.org/api/v2/search"
USER_AGENT = "QA-Agent-portfolio/0.1 (ashwin.apps00@gmail.com)"  # OFF asks for an identifying UA
CATEGORIES = ["en:bodybuilding-supplements", "en:protein-bars"]
COUNTRY = "en:germany"
PAGE_SIZE = 100
MAX_PAGES_PER_CATEGORY = 3
FIELDS = [
    "code", "product_name", "product_name_de", "product_name_en", "brands", "quantity",
    "serving_size", "categories_tags", "countries_tags", "ingredients_text",
    "ingredients_text_de", "ingredients_text_en", "allergens_tags", "traces_tags",
    "nutriments", "lang", "last_modified_t",
]
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def get_json(params: dict, retries: int = 6) -> dict:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            wait = 20 * attempt
            print(f"  attempt {attempt} failed ({exc!r}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def fetch_category(category: str) -> tuple[list[dict], int]:
    products, total = [], 0
    for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
        data = get_json({
            "categories_tags": category,
            "countries_tags": COUNTRY,
            "fields": ",".join(FIELDS),
            "page_size": PAGE_SIZE,
            "page": page,
            "sort_by": "unique_scans_n",  # most-scanned first: real, commonly bought products
        })
        total = data.get("count", 0)
        batch = data.get("products", [])
        products.extend(batch)
        print(f"{category} page {page}: {len(batch)} products (category total in {COUNTRY}: {total})")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(7)  # search API is rate-limited (~10 req/min)
    return products, total


def main() -> None:
    """One file per category, so a failure part-way keeps what was already fetched.
    Re-running skips categories that already have a file for today."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for category in CATEGORIES:
        fetched_at = datetime.now(timezone.utc)
        out = OUT_DIR / f"off_{category.split(':')[1]}_{fetched_at:%Y-%m-%d}.json"
        if out.exists():
            print(f"skip {category}: {out.name} exists")
            continue
        products, total = fetch_category(category)
        out.write_text(json.dumps({
            "source": "Open Food Facts, https://openfoodfacts.org",
            "licence": "ODbL 1.0 (database), DbCL 1.0 (contents)",
            "fetched_at": fetched_at.isoformat(),
            "query": {"category": category, "country": COUNTRY, "sort_by": "unique_scans_n",
                      "max_products": PAGE_SIZE * MAX_PAGES_PER_CATEGORY},
            "category_total": total,
            "products": products,
        }, ensure_ascii=False, indent=1))
        print(f"wrote {len(products)} products to {out}")
        time.sleep(7)


if __name__ == "__main__":
    main()
