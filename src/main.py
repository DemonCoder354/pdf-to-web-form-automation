"""PDF to web form: read client data from PDF pages and enter it into a form.

Commands
--------
setup    Teach the program by example: give the values of the five fields as
         they appear on one sample page. The program finds them, remembers the
         label next to each value and the value's shape, and saves template.json.
extract  Read every page of a PDF with the template and write a CSV.
preview  Save a PNG of one page with the found labels and values boxed, to
         check the template visually.
run      Extract, then type every complete record into the web form and click
         "Save record", one client after another. Writes a report CSV.

Examples
--------
    python main.py setup --pdf sample_data/clients.pdf --page 1
    python main.py setup --pdf sample_data/clients.pdf --examples sample_data/examples.json
    python main.py extract --pdf sample_data/clients.pdf
    python main.py preview --pdf sample_data/clients.pdf --page 5
    python main.py run --pdf sample_data/clients.pdf --headed --slow-mo 150
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from extractor import Template, extract_page, learn_template, read_pdf

# In the packaged .exe (PyInstaller) data files and outputs live next to the exe.
BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DEFAULT_TEMPLATE = BASE_DIR / "template.json"
DEFAULT_FORM = BASE_DIR / "demo_form" / "index.html"

# The five fields of the web form, in the order they are asked during setup.
FIELDS = {
    "full_name": "Full name",
    "email": "Email",
    "phone": "Phone",
    "policy_number": "Policy number",
    "city": "City",
}

log = logging.getLogger("pdf_form_filler")


def setup_logging() -> Path:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log_file


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #

def cmd_setup(args) -> int:
    pages = read_pdf(args.pdf)
    if args.examples:
        examples = json.loads(Path(args.examples).read_text(encoding="utf-8"))
    else:
        segs = pages[args.page - 1]
        print(f"\nText on page {args.page} of {args.pdf.name}:\n")
        for s in sorted(segs, key=lambda s: (round(s.top), s.x0)):
            print(f"  {s.text}")
        print("\nCopy the value of each field exactly as it appears above.\n")
        examples = {name: input(f"{title}: ").strip() for name, title in FIELDS.items()}

    missing = [f for f in FIELDS if not examples.get(f)]
    if missing:
        print(f"Missing examples for: {', '.join(missing)}")
        return 2
    try:
        tpl = learn_template(pages, args.page, {f: examples[f] for f in FIELDS}, source_pdf=args.pdf.name)
    except ValueError as exc:
        print(f"Setup failed: {exc}")
        return 2

    print(f"\nLearned from page {args.page}:")
    for r in tpl.fields:
        where = "right of" if r.direction == "left" else "under"
        print(f"  {r.field:<14} {r.kind:<6} {where} label '{r.label}'   (example: {r.example})")
    tpl.save(args.template)
    print(f"\nSaved template to {args.template}")
    return 0


# --------------------------------------------------------------------------- #
# extract / run
# --------------------------------------------------------------------------- #

def extract_all(pdf: Path, tpl: Template) -> tuple[list[tuple[int, dict]], list[tuple[int, dict, str]]]:
    """Return (good records, skipped pages as (page, partial record, reason))."""
    good, skipped = [], []
    for page_no, segments in enumerate(read_pdf(pdf), start=1):
        res = extract_page(tpl, segments)
        problems = [f"{name}: {r.note}" for name, r in res.items() if not r.ok]
        notes = [f"{name}: {r.note}" for name, r in res.items() if r.ok and r.note]
        record = {name: r.value for name, r in res.items()}
        if problems:
            skipped.append((page_no, record, "; ".join(problems)))
            log.warning("Page %-2d SKIPPED %s: %s", page_no, record["full_name"] or "?", "; ".join(problems))
            continue
        for n in notes:
            log.info("Page %-2d note: %s", page_no, n)
        good.append((page_no, record))
        log.info("Page %-2d %s", page_no, "  |  ".join(record[f] for f in FIELDS))
    return good, skipped


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def load_template(path: Path) -> Template | None:
    if not path.exists():
        print(f"No template at {path}. Run `python main.py setup ...` first.")
        return None
    return Template.load(path)


def cmd_extract(args) -> int:
    tpl = load_template(args.template)
    if not tpl:
        return 2
    setup_logging()
    log.info("Reading %s with template %s", args.pdf.name, args.template.name)
    good, skipped = extract_all(args.pdf, tpl)
    out = args.csv or BASE_DIR / "output" / f"extracted_{datetime.now():%Y%m%d_%H%M%S}.csv"
    rows = [[p] + [rec[f] for f in FIELDS] + ["ok", ""] for p, rec in good]
    rows += [[p] + [rec[f] for f in FIELDS] + ["skipped", why] for p, rec, why in skipped]
    write_csv(out, ["page", *FIELDS, "status", "note"], sorted(rows, key=lambda r: r[0]))
    log.info("Done: %d records extracted, %d pages skipped. CSV: %s", len(good), len(skipped), out)
    return 0


def cmd_run(args) -> int:
    tpl = load_template(args.template)
    if not tpl:
        return 2
    setup_logging()
    started = time.perf_counter()
    log.info("Reading %s with template %s", args.pdf.name, args.template.name)
    good, skipped = extract_all(args.pdf, tpl)
    if args.limit:
        good = good[: args.limit]
    if args.dry_run:
        log.info("Dry run: %d records ready, %d skipped. Nothing was typed.", len(good), len(skipped))
        return 0

    from filler import fill_records  # imported here so `extract` works without Playwright

    form_url = args.form_url or DEFAULT_FORM.resolve().as_uri()
    log.info("Opening %s and entering %d records", form_url, len(good))
    results = fill_records(
        good, form_url, headed=args.headed, slow_mo=args.slow_mo, type_delay=args.type_delay, channel=args.browser,
        video_dir=args.video_dir, screenshot=args.screenshot,
    )

    by_page = {r.page: r for r in results}
    rows = [[p] + [rec[f] for f in FIELDS] + [by_page[p].status, by_page[p].message] for p, rec in good]
    rows += [[p] + [rec[f] for f in FIELDS] + ["skipped", why] for p, rec, why in skipped]
    report = BASE_DIR / "output" / f"report_{datetime.now():%Y%m%d_%H%M%S}.csv"
    write_csv(report, ["page", *FIELDS, "status", "note"], sorted(rows, key=lambda r: r[0]))

    saved = sum(r.status == "saved" for r in results)
    log.info("Finished: %d saved, %d rejected by the form, %d skipped in the PDF, in %.1f s",
             saved, len(results) - saved, len(skipped), time.perf_counter() - started)
    log.info("Report: %s", report.relative_to(BASE_DIR))
    return 0


def cmd_preview(args) -> int:
    """Save a PNG of one page with the found labels (blue) and values (green) boxed."""
    import pdfplumber

    tpl = load_template(args.template)
    if not tpl:
        return 2
    pages = read_pdf(args.pdf)
    results = extract_page(tpl, pages[args.page - 1])
    with pdfplumber.open(args.pdf) as pdf:
        img = pdf.pages[args.page - 1].to_image(resolution=110)
        for name, r in results.items():
            if r.label_seg:
                s = r.label_seg
                img.draw_rect((s.x0 - 2, s.top - 2, s.x1 + 2, s.bottom + 2), fill=(37, 87, 214, 40), stroke=(37, 87, 214), stroke_width=2)
            if r.value_seg:
                s = r.value_seg
                color = (21, 127, 60) if r.ok else (196, 50, 43)
                img.draw_rect((s.x0 - 2, s.top - 2, s.x1 + 2, s.bottom + 2), fill=color + (40,), stroke=color, stroke_width=2)
            print(f"  {name:<14} {'OK ' if r.ok else 'BAD'} {r.value!r} {r.note}")
        out = args.out or BASE_DIR / "output" / f"preview_page{args.page}.png"
        out.parent.mkdir(exist_ok=True)
        img.save(str(out))
    print(f"Saved {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read client data from PDF pages and enter it into a web form.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--pdf", type=Path, required=True, help="PDF with one client per page")
        p.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="template file (default: template.json)")

    p = sub.add_parser("setup", help="learn field positions from example values")
    common(p)
    p.add_argument("--page", type=int, default=1, help="sample page number (default: 1)")
    p.add_argument("--examples", help="JSON file with example values; asks interactively if omitted")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("extract", help="extract all pages to CSV")
    common(p)
    p.add_argument("--csv", type=Path, help="output CSV path")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("preview", help="save a PNG of one page with the found fields highlighted")
    common(p)
    p.add_argument("--page", type=int, default=1, help="page number (default: 1)")
    p.add_argument("--out", type=Path, help="output PNG path")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("run", help="extract and enter every record into the web form")
    common(p)
    p.add_argument("--form-url", help="form address (default: local demo_form/index.html)")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--browser", choices=["msedge", "chrome"],
                   help="use the installed Edge or Chrome instead of Playwright's own Chromium")
    p.add_argument("--slow-mo", type=int, default=0, help="ms pause between browser actions")
    p.add_argument("--type-delay", type=int, default=0, help="ms between keystrokes (0 = paste instantly)")
    p.add_argument("--limit", type=int, help="enter only the first N records")
    p.add_argument("--dry-run", action="store_true", help="extract and validate only, do not open the browser")
    p.add_argument("--video-dir", type=Path, help="record a video of the browser into this folder")
    p.add_argument("--screenshot", type=Path, help="save a screenshot of the form at the end")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


def menu() -> int:
    """Simple menu for starting the program with a double click (no arguments)."""
    pdf = str(BASE_DIR / "sample_data" / "clients.pdf")
    browser = ["--browser", "msedge"] if sys.platform == "win32" else []
    choices = {
        "1": ("Fill the web form from the PDF (browser window, slowed down)",
              ["run", "--pdf", pdf, "--headed", "--slow-mo", "150", "--type-delay", "20", *browser]),
        "2": ("Setup by example (type the 5 values from page 1)", ["setup", "--pdf", pdf]),
        "3": ("Extract all pages to CSV", ["extract", "--pdf", pdf]),
        "4": ("Save a preview image of page 1", ["preview", "--pdf", pdf]),
    }
    print("PDF to Web Form Automation\n")
    for key, (title, _) in choices.items():
        print(f"  {key}. {title}")
    picked = input("\nChoose 1-4 (Enter = 1): ").strip() or "1"
    if picked not in choices:
        print("Unknown choice.")
        return 2
    print()
    return main(choices[picked][1])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    # Started without arguments, e.g. by double-clicking main.py:
    # show a menu and keep the window open at the end.
    try:
        code = menu()
    except Exception:  # show the error instead of closing the window
        import traceback
        traceback.print_exc()
        code = 1
    input("\nPress Enter to close...")
    sys.exit(code)
