"""Learn where fields live in a PDF from examples, then extract them from every page.

How it works
------------
1. Every page is split into *segments*: runs of words on one line that sit
   close together ("Full name", "Olivia Bennett", "POLICY NUMBER", ...).
2. Text that repeats on most pages of the sample PDF is *static* (labels,
   headings). Text that changes from page to page is data.
3. Setup: for each example value the user gives (e.g. "Olivia Bennett" for the
   full name), the value is found on the sample page and the nearest static
   label is recorded, together with where the value sits relative to it
   (same line to the right, or underneath) and the shape of the value
   (email, phone, code like HL-482137, plain text...).
4. Extraction: on every page the label is found again (fuzzy match, so small
   wording changes are tolerated), the value is read from the same relative
   place, and it is checked against the learned shape. Absolute positions do
   not matter, so the method survives rows and blocks moving up or down.

Pure Python + pdfplumber, no GUI, unit-tested in tests/.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pdfplumber

WORD_GAP = 6.0          # pt; a bigger gap between words starts a new segment
LINE_TOLERANCE = 3.0    # pt; words whose bottoms differ less are on one line
LABEL_MATCH = 0.85      # minimum fuzzy score to accept a label on a new page
STATIC_SHARE = 0.6      # text on >= 60% of pages counts as a static label


@dataclass
class Segment:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float

    @property
    def norm(self) -> str:
        return normalize(self.text)


@dataclass
class FieldRule:
    field: str
    example: str
    label: str
    direction: str              # "left" (label left of value) or "above"
    dx: float                   # value.x0 - label anchor x
    dy: float                   # value.top - label.top
    label_pos: list[float]      # [x0, top] on the sample page
    value_pos: list[float]      # [x0, top] on the sample page
    pattern: str                # regex the value must match
    kind: str                   # email / phone / code / date / text / any


@dataclass
class Template:
    source_pdf: str
    sample_page: int
    fields: list[FieldRule] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Template":
        raw = json.loads(path.read_text(encoding="utf-8"))
        rules = [FieldRule(**r) for r in raw.pop("fields")]
        return cls(fields=rules, **raw)


# --------------------------------------------------------------------------- #
# Reading pages
# --------------------------------------------------------------------------- #

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(":").strip().casefold()


def words_to_segments(words: list[dict]) -> list[Segment]:
    """Group pdfplumber words into lines, then split lines at wide gaps."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["bottom"], w["x0"])):
        if lines and abs(lines[-1][0]["bottom"] - w["bottom"]) <= LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])

    segments = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        run = [line[0]]
        for w in line[1:]:
            if w["x0"] - run[-1]["x1"] > WORD_GAP:
                segments.append(_make_segment(run))
                run = [w]
            else:
                run.append(w)
        segments.append(_make_segment(run))
    return segments


def _make_segment(words: list[dict]) -> Segment:
    return Segment(
        text=" ".join(w["text"] for w in words),
        x0=min(w["x0"] for w in words),
        x1=max(w["x1"] for w in words),
        top=min(w["top"] for w in words),
        bottom=max(w["bottom"] for w in words),
        size=max(w.get("size", 0) for w in words),
    )


def read_pdf(pdf_path: Path) -> list[list[Segment]]:
    """Return the segments of every page."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1.5, extra_attrs=["size"])
            pages.append(words_to_segments(words))
    return pages


def static_texts(pages: list[list[Segment]]) -> set[str]:
    """Texts that appear on most pages: labels and headings, not data."""
    if len(pages) < 2:
        return set()
    counts = Counter(t for segs in pages for t in {s.norm for s in segs})
    need = max(2, round(len(pages) * STATIC_SHARE))
    return {t for t, n in counts.items() if n >= need}


# --------------------------------------------------------------------------- #
# Value shapes
# --------------------------------------------------------------------------- #

EMAIL_RE = r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"
PHONE_RE = r"\+?[\d\s().-]{7,}"
TEXT_RE = r"[^\d@]*[A-Za-z][^\d@]*"


def infer_pattern(value: str) -> tuple[str, str]:
    """Guess the kind of value and a regex that similar values will match."""
    v = value.strip()
    if re.fullmatch(EMAIL_RE, v):
        return "email", EMAIL_RE
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return "date", r"\d{4}-\d{2}-\d{2}"
    if re.fullmatch(PHONE_RE, v) and sum(c.isdigit() for c in v) >= 7:
        return "phone", PHONE_RE
    if " " not in v and re.search(r"\d", v):
        return "code", shape_regex(v)
    if re.fullmatch(TEXT_RE, v):
        return "text", TEXT_RE
    return "any", r".+"


def shape_regex(value: str) -> str:
    """'HL-482137' -> '[A-Z]{2}-\\d{6}'."""
    out = []
    for run in re.finditer(r"[A-Z]+|[a-z]+|\d+|.", value):
        s = run.group()
        if s.isdigit():
            out.append(rf"\d{{{len(s)}}}")
        elif s.isupper():
            out.append(f"[A-Z]{{{len(s)}}}")
        elif s.islower():
            out.append(f"[a-z]{{{len(s)}}}")
        else:
            out.append(re.escape(s))
    return "".join(out)


def matches(rule: FieldRule, value: str) -> bool:
    return bool(value) and re.fullmatch(rule.pattern, value) is not None


# --------------------------------------------------------------------------- #
# Learning from examples
# --------------------------------------------------------------------------- #

def _overlaps_x(a: Segment, b: Segment, pad: float = 4) -> bool:
    return a.x0 - pad < b.x1 and b.x0 - pad < a.x1


def _is_label(seg: Segment, value: Segment, static: set[str]) -> bool:
    if static:
        return seg.norm in static
    # Single-page fallback: labels are usually smaller or end with a colon.
    return seg.size < value.size - 0.5 or seg.text.rstrip().endswith(":")


def find_label(value: Segment, segments: list[Segment], static: set[str]) -> tuple[Segment, str] | None:
    """Nearest label: on the same line to the left, otherwise just above."""
    others = [s for s in segments if s is not value and _is_label(s, value, static)]
    left = [s for s in others
            if abs(s.bottom - value.bottom) <= LINE_TOLERANCE and s.x1 <= value.x0 + 1]
    if left:
        return max(left, key=lambda s: s.x1), "left"
    above = [s for s in others
             if s.bottom <= value.top + 1 and value.top - s.bottom < 14 and _overlaps_x(s, value)]
    if above:
        return max(above, key=lambda s: s.bottom), "above"
    return None


def find_example(example: str, segments: list[Segment]) -> Segment:
    target = normalize(example)
    hits = [s for s in segments if s.norm == target]
    if not hits:
        close = sorted(segments, key=lambda s: -SequenceMatcher(None, s.norm, target).ratio())[:3]
        hint = ", ".join(repr(s.text) for s in close)
        raise ValueError(f"'{example}' was not found on the sample page. Closest texts: {hint}")
    return hits[0]


def learn_field(name: str, value: Segment, segments: list[Segment], static: set[str]) -> FieldRule:
    """Learn one field from the segment that holds its example value."""
    found = find_label(value, segments, static)
    if not found:
        raise ValueError(f"No label found next to '{value.text}' for field '{name}'")
    label, direction = found
    kind, pattern = infer_pattern(value.text)
    return FieldRule(
        field=name,
        example=value.text,
        label=label.text.rstrip(":"),
        direction=direction,
        dx=round(value.x0 - (label.x1 if direction == "left" else label.x0), 2),
        dy=round(value.top - label.top, 2),
        label_pos=[round(label.x0, 2), round(label.top, 2)],
        value_pos=[round(value.x0, 2), round(value.top, 2)],
        pattern=pattern,
        kind=kind,
    )


def learn_template(pages: list[list[Segment]], page_no: int, examples: dict[str, str],
                   source_pdf: str = "") -> Template:
    """Build a template from example values found on one sample page (1-based)."""
    if not 1 <= page_no <= len(pages):
        raise ValueError(f"Page {page_no} does not exist (the PDF has {len(pages)} pages)")
    segments = pages[page_no - 1]
    static = static_texts(pages)
    tpl = Template(source_pdf=source_pdf, sample_page=page_no)
    for name, example in examples.items():
        tpl.fields.append(learn_field(name, find_example(example, segments), segments, static))
    return tpl


def segment_at(segments: list[Segment], x: float, y: float, pad: float = 2) -> Segment | None:
    """The text segment under a point (PDF points, origin top-left), for click-to-pick."""
    hits = [s for s in segments if s.x0 - pad <= x <= s.x1 + pad and s.top - pad <= y <= s.bottom + pad]
    return min(hits, key=lambda s: (s.x1 - s.x0) * (s.bottom - s.top), default=None)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

@dataclass
class FieldResult:
    value: str
    ok: bool
    note: str = ""
    label_seg: Segment | None = None   # where things were found (for previews)
    value_seg: Segment | None = None


def _locate_label(rule: FieldRule, segments: list[Segment]) -> Segment | None:
    target = normalize(rule.label)
    scored = []
    for s in segments:
        score = SequenceMatcher(None, s.norm, target).ratio()
        if score >= LABEL_MATCH:
            dist = abs(s.x0 - rule.label_pos[0]) + abs(s.top - rule.label_pos[1])
            scored.append((-score, dist, s))
    return min(scored, key=lambda t: (t[0], t[1]))[2] if scored else None


def _value_near_label(rule: FieldRule, label: Segment, segments: list[Segment]) -> Segment | None:
    if rule.direction == "left":
        expect_x = label.x1 + rule.dx
        cands = [s for s in segments
                 if abs(s.bottom - label.bottom) <= LINE_TOLERANCE and s.x0 > label.x1
                 and abs(s.x0 - expect_x) <= 40]
        return min(cands, key=lambda s: abs(s.x0 - expect_x), default=None)
    expect_x, expect_top = label.x0 + rule.dx, label.top + rule.dy
    cands = [s for s in segments
             if s.top > label.top + 1 and abs(s.top - expect_top) <= 8 and abs(s.x0 - expect_x) <= 40]
    return min(cands, key=lambda s: abs(s.top - expect_top) + abs(s.x0 - expect_x), default=None)


def extract_field(rule: FieldRule, segments: list[Segment]) -> FieldResult:
    label = _locate_label(rule, segments)
    if label is not None:
        seg = _value_near_label(rule, label, segments)
        if seg is None:
            return FieldResult("", False, f"label '{label.text}' found but no value next to it", label)
        if matches(rule, seg.text):
            return FieldResult(seg.text, True, "", label, seg)
        return FieldResult(seg.text, False, f"'{seg.text}' does not look like a {rule.kind}", label, seg)

    # Label missing: take the closest value of the right shape near the old spot.
    x, top = rule.value_pos
    cands = [s for s in segments if matches(rule, s.text) and abs(s.x0 - x) + abs(s.top - top) < 120]
    if cands:
        best = min(cands, key=lambda s: abs(s.x0 - x) + abs(s.top - top))
        return FieldResult(best.text, True, "label not found, used position fallback", None, best)
    return FieldResult("", False, f"label '{rule.label}' not found")


def extract_page(tpl: Template, segments: list[Segment]) -> dict[str, FieldResult]:
    return {rule.field: extract_field(rule, segments) for rule in tpl.fields}
