import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extractor import infer_pattern, learn_template, read_pdf, shape_regex, words_to_segments, extract_page  # noqa: E402

PDF = ROOT / "sample_data" / "clients.pdf"
EXAMPLES = json.loads((ROOT / "sample_data" / "examples.json").read_text())


@pytest.fixture(scope="module")
def pages():
    return read_pdf(PDF)


@pytest.fixture(scope="module")
def template(pages):
    return learn_template(pages, 1, EXAMPLES)


def test_shape_regex():
    assert shape_regex("HL-482137") == r"[A-Z]{2}\-\d{6}"


@pytest.mark.parametrize("value, kind", [
    ("olivia.bennett@example.com", "email"),
    ("+1 (555) 010-2231", "phone"),
    ("HL-482137", "code"),
    ("2026-07-01", "date"),
    ("Salt Lake City", "text"),
])
def test_infer_pattern(value, kind):
    assert infer_pattern(value)[0] == kind


def test_segments_split_on_wide_gaps():
    words = [
        {"text": "Full", "x0": 50, "x1": 66, "top": 10, "bottom": 18, "size": 8},
        {"text": "name", "x0": 68, "x1": 88, "top": 10, "bottom": 18, "size": 8},
        {"text": "Olivia", "x0": 145, "x1": 175, "top": 9, "bottom": 18.5, "size": 10},
        {"text": "Bennett", "x0": 178, "x1": 215, "top": 9, "bottom": 18.5, "size": 10},
    ]
    assert [s.text for s in words_to_segments(words)] == ["Full name", "Olivia Bennett"]


def test_learns_both_layouts(template):
    rules = {r.field: r for r in template.fields}
    assert rules["full_name"].label == "Full name" and rules["full_name"].direction == "left"
    assert rules["policy_number"].label == "POLICY NUMBER" and rules["policy_number"].direction == "above"


def test_extracts_every_page_despite_shifted_layout(pages, template):
    page8 = {k: r.value for k, r in extract_page(template, pages[7]).items()}
    assert page8 == {
        "full_name": "Lucas Ferreira",
        "email": "lucas.ferreira@example.com",   # not the agent's or emergency contact's email
        "phone": "+1 (555) 010-7742",
        "policy_number": "HL-482213",
        "city": "Salt Lake City",
    }
    ok_pages = [i for i, p in enumerate(pages, 1) if all(r.ok for r in extract_page(template, p).values())]
    assert ok_pages == [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13]


def test_missing_value_is_flagged(pages, template):
    res = extract_page(template, pages[6])
    assert not res["phone"].ok
    assert res["full_name"].value == "Aisha Rahman"


def test_unknown_example_gives_helpful_error(pages):
    with pytest.raises(ValueError, match="not found"):
        learn_template(pages, 1, {"full_name": "Nobody Here"})
