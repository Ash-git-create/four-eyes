from pathlib import Path

import pytest

from retrieval.chunking import policy_to_chunks, product_to_chunk

PRODUCT = {
    "code": "4001234567890",
    "product_name": "Protein Bar Cookie",
    "brands": "ExampleBrand",
    "quantity": "45 g",
    "categories_tags": ["en:snacks", "en:protein-bars", "de:riegel"],
    "ingredients_text": "Milchprotein, Haferflocken, Süßungsmittel",
    "allergens_tags": ["en:milk", "en:gluten", "en:milk"],
    "traces_tags": ["en:nuts"],
    "nutriments": {"proteins_100g": 33.0, "sugars_100g": 1.5, "salt_100g": "n/a"},
    "lang": "de",
}


def test_product_chunk_fields():
    c = product_to_chunk(PRODUCT)
    assert c.chunk_id == c.source_id == "off:4001234567890"
    assert c.source_type == "product"
    assert c.title == "Protein Bar Cookie (ExampleBrand)"
    assert c.source_url == "https://world.openfoodfacts.org/product/4001234567890"
    assert c.lang == "de"


def test_product_chunk_text_is_readable():
    text = product_to_chunk(PRODUCT).text
    assert "Allergens: milk, gluten" in text          # tag prefix stripped, duplicates dropped
    assert "May contain traces of: nuts" in text
    assert "Categories: snacks, protein bars" in text  # non-English tags left out
    assert "Protein 33 g; Sugars 1.5 g" in text
    assert "Salt" not in text                          # non-numeric nutrient values skipped
    assert "Milchprotein" in text                      # original-language ingredients kept


def test_ingredients_come_last_so_truncation_cannot_cut_allergens():
    lines = product_to_chunk(PRODUCT).text.splitlines()
    assert lines[-1].startswith("Ingredients:")


def test_product_without_allergens_says_none_declared():
    assert "Allergens: none declared" in product_to_chunk({**PRODUCT, "allergens_tags": []}).text


def test_product_name_falls_back_to_language_fields():
    c = product_to_chunk({**PRODUCT, "product_name": "", "product_name_de": "Eiweißriegel"})
    assert c.title.startswith("Eiweißriegel")


@pytest.mark.parametrize("patch", [{"product_name": ""}, {"product_name": "  "}, {"code": ""}])
def test_product_without_name_or_code_is_skipped(patch):
    assert product_to_chunk({**PRODUCT, **patch}) is None


def test_policy_split_on_sections(tmp_path: Path):
    doc = tmp_path / "returns.md"
    doc.write_text("# Returns\n\n> SAMPLE notice\n\n## Return window\nThirty days.\n\n## Opened products\nNo.\n")
    chunks = policy_to_chunks(doc, "sample-returns")
    assert [c.chunk_id for c in chunks] == ["sample-returns#return-window", "sample-returns#opened-products"]
    assert chunks[0].text == "Returns - Return window\nThirty days."
    assert all("SAMPLE notice" not in c.text for c in chunks)


def test_policy_duplicate_heading_fails_loudly(tmp_path: Path):
    doc = tmp_path / "x.md"
    doc.write_text("# X\n## A\none\n## A\ntwo\n")
    with pytest.raises(ValueError):
        policy_to_chunks(doc, "x")
