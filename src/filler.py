"""Type extracted client records into the web form with Playwright.

For each record: fill the five inputs, click "Save record", then read the page
to confirm the client appeared in the "Saved clients" list. If the form shows
an error instead (for example a duplicate policy number), the record is marked
as rejected and the run continues with the next one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

log = logging.getLogger("pdf_form_filler")

# Field name in the template -> CSS selector of the input on the page.
SELECTORS = {
    "full_name": "#full_name",
    "email": "#email",
    "phone": "#phone",
    "policy_number": "#policy_number",
    "city": "#city",
}
SAVE_BUTTON = "#save-btn"
STATUS = "#status"
SAVED_ROWS = "#records li"


@dataclass
class FillResult:
    page: int
    status: str      # saved / rejected / error
    message: str


def fill_records(records: list[tuple[int, dict[str, str]]], form_url: str, *, headed: bool = False,
                 slow_mo: int = 0, type_delay: int = 0, channel: str | None = None,
                 on_result=None, should_stop=None, video_dir: Path | None = None,
                 screenshot: Path | None = None) -> list[FillResult]:
    """records: list of (pdf page number, {field: value}).

    on_result(FillResult) is called after each record, should_stop() before each
    one (used by the desktop app for its progress bar and Stop button).
    channel: "msedge" or "chrome" uses the browser already installed on the
    computer instead of the one downloaded by `playwright install`.
    """
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, slow_mo=slow_mo, channel=channel)
        context = browser.new_context(
            viewport={"width": 1280, "height": 760},
            record_video_dir=str(video_dir) if video_dir else None,
            record_video_size={"width": 1280, "height": 760} if video_dir else None,
        )
        page = context.new_page()
        page.goto(form_url)
        page.wait_for_selector(SAVE_BUTTON)

        for page_no, rec in records:
            if should_stop and should_stop():
                log.warning("Stopped by the user")
                break
            try:
                results.append(_fill_one(page, page_no, rec, type_delay))
            except PlaywrightTimeout as exc:
                results.append(FillResult(page_no, "error", f"timeout: {exc}"))
            r = results[-1]
            level = logging.INFO if r.status == "saved" else logging.WARNING
            log.log(level, "Page %-2d %-16s %s %s", page_no, rec.get("full_name", ""), r.status.upper(), r.message)
            if on_result:
                on_result(r)

        page.wait_for_timeout(800)
        if screenshot:
            page.screenshot(path=str(screenshot), full_page=True)
        context.close()
        browser.close()
    return results


def _fill_one(page, page_no: int, rec: dict[str, str], type_delay: int) -> FillResult:
    before = page.locator(SAVED_ROWS).count()
    for name, selector in SELECTORS.items():
        box = page.locator(selector)
        box.fill("")
        if type_delay:
            box.press_sequentially(rec[name], delay=type_delay)
        else:
            box.fill(rec[name])
    page.click(SAVE_BUTTON)
    page.wait_for_function(
        "([sel, n]) => document.querySelectorAll(sel).length > n"
        " || document.querySelector('#status').classList.contains('error')",
        arg=[SAVED_ROWS, before], timeout=5000,
    )
    if page.locator(SAVED_ROWS).count() > before:
        return FillResult(page_no, "saved", "")
    return FillResult(page_no, "rejected", page.inner_text(STATUS).strip())
