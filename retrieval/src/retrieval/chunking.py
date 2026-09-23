"""Turn raw sources into chunks: one per product, one per policy section.

Chunk ids are stable across re-ingestion (product barcode, policy heading slug),
because the Day 3 evaluation set refers to them as gold labels.
"""
import re
from dataclasses import dataclass
from pathlib import Path

NUTRIENTS = [
    ("energy-kcal_100g", "Energy", "kcal"),
    ("proteins_100g", "Protein", "g"),
    ("carbohydrates_100g", "Carbohydrates", "g"),
    ("sugars_100g", "Sugars", "g"),
    ("fat_100g", "Fat", "g"),
    ("saturated-fat_100g", "Saturated fat", "g"),
    ("fiber_100g", "Fibre", "g"),
    ("salt_100g", "Salt", "g"),
]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_id: str
    source_type: str  # "product" | "policy"
    title: str
    text: str
    source_url: str
    lang: str | None


def _first(p: dict, *keys: str) -> str:
    for k in keys:
        if v := (p.get(k) or "").strip():
            return v
    return ""


def _tag_names(tags: list[str] | None) -> list[str]:
    """'en:milk' -> 'milk'. Keeps order, drops duplicates."""
    names = [t.split(":", 1)[-1].replace("-", " ") for t in tags or []]
    return list(dict.fromkeys(names))


def product_to_chunk(p: dict) -> Chunk | None:
    """Returns None for products without a name: nothing useful to retrieve or cite."""
    name = _first(p, "product_name", "product_name_de", "product_name_en")
    if not name or not p.get("code"):
        return None
    code = p["code"]
    brand = _first(p, "brands")
    title = f"{name} ({brand})" if brand else name

    lines = [f"Product: {title}"]
    if q := _first(p, "quantity"):
        lines.append(f"Quantity: {q}")
    if s := _first(p, "serving_size"):
        lines.append(f"Serving size: {s}")
    categories = [t.split(":", 1)[1].replace("-", " ") for t in p.get("categories_tags") or [] if t.startswith("en:")]
    if categories:
        lines.append(f"Categories: {', '.join(categories)}")
    lines.append(f"Allergens: {', '.join(_tag_names(p.get('allergens_tags'))) or 'none declared'}")
    if traces := _tag_names(p.get("traces_tags")):
        lines.append(f"May contain traces of: {', '.join(traces)}")
    n = p.get("nutriments") or {}
    facts = [f"{label} {n[key]:g} {unit}" for key, label, unit in NUTRIENTS if isinstance(n.get(key), (int, float))]
    if facts:
        lines.append(f"Nutrition per 100 g: {'; '.join(facts)}")
    # Ingredients last: the embedding model truncates at 512 tokens, and a long
    # ingredient list must not push allergens and nutrition out of the window.
    if ing := _first(p, "ingredients_text", "ingredients_text_de", "ingredients_text_en"):
        lines.append(f"Ingredients: {ing}")

    return Chunk(
        chunk_id=f"off:{code}",
        source_id=f"off:{code}",
        source_type="product",
        title=title,
        text="\n".join(lines),
        source_url=f"https://world.openfoodfacts.org/product/{code}",
        lang=p.get("lang"),
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def policy_to_chunks(path: Path, source_id: str) -> list[Chunk]:
    """Split a markdown policy on '## ' headings. Blockquote lines (the SAMPLE/DRAFT
    notice) are metadata, not content, so they are left out of chunk text."""
    title, sections, current = "", [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            current = [line[3:].strip(), []]
            sections.append(current)
        elif current is not None and not line.startswith(">"):
            current[1].append(line)

    chunks, seen = [], set()
    for heading, body in sections:
        chunk_id = f"{source_id}#{_slug(heading)}"
        if chunk_id in seen:
            raise ValueError(f"duplicate heading {heading!r} in {path}")
        seen.add(chunk_id)
        chunks.append(Chunk(
            chunk_id=chunk_id,
            source_id=source_id,
            source_type="policy",
            title=f"{title} - {heading}",
            text=f"{title} - {heading}\n" + "\n".join(body).strip(),
            source_url=str(path.as_posix()),
            lang="en",
        ))
    return chunks
