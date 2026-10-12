"""Desktop window for the PDF to Web Form tool (no console needed).

Double-click this file on Windows (.pyw files open without a console window),
or run `python app.pyw` anywhere.

Tab 1, Calibrate: the PDF page is shown as a picture. Choose a field on the
left, then click its value on the page. The program finds the label next to
it and learns where to look on every other page.
Tab 2, Fill the form: reads every page and types the clients into the web
form in a browser, with a live table and progress bar.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pypdfium2 as pdfium
from PIL import ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extractor import Template, extract_page, learn_field, read_pdf, segment_at, static_texts  # noqa: E402
from main import BASE_DIR, DEFAULT_FORM, DEFAULT_TEMPLATE, FIELDS, extract_all, log, write_csv  # noqa: E402

SCALE = 1.3   # PDF points -> screen pixels
COLORS = {"full_name": "#2557d6", "email": "#b4459a", "phone": "#c76a00",
          "policy_number": "#0f8a7a", "city": "#7a4fd0"}
BROWSERS = {"Microsoft Edge": "msedge", "Google Chrome": "chrome", "Built-in Chromium": None}


class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(("log", self.format(record)))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF to Web Form")
        self.geometry("1180x820")
        self.minsize(980, 640)

        self.events: queue.Queue = queue.Queue()
        self.pdf_path: Path | None = None
        self.pages = []            # segments per page
        self.static: set[str] = set()
        self.doc = None
        self.page_no = 1
        self.photo = None
        self.rules = {}            # field -> FieldRule picked in this session or loaded
        self.picks = {}            # field -> (page_no, Segment) clicked in this session
        self.current = tk.StringVar(value="full_name")
        self.stop_flag = threading.Event()
        self.worker = None
        self.last_report: Path | None = None

        self._setup_logging()
        self._build_top()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tab_cal = ttk.Frame(nb)
        self.tab_run = ttk.Frame(nb)
        nb.add(self.tab_cal, text="  1. Calibrate  ")
        nb.add(self.tab_run, text="  2. Fill the form  ")
        self._build_calibrate()
        self._build_run()

        if DEFAULT_TEMPLATE.exists():
            tpl = Template.load(DEFAULT_TEMPLATE)
            self.rules = {r.field: r for r in tpl.fields}
        sample = BASE_DIR / "sample_data" / "clients.pdf"
        if sample.exists():
            self.load_pdf(sample)
        self._refresh_fields()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ setup
    def _setup_logging(self):
        handler = QueueHandler(self.events)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

    def _build_top(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Label(bar, text="PDF file:").pack(side="left")
        self.pdf_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.pdf_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text="Open PDF...", command=self.choose_pdf).pack(side="left")

    def _build_calibrate(self):
        left = ttk.Frame(self.tab_cal, width=310)
        left.pack(side="left", fill="y", padx=(10, 6), pady=10)
        left.pack_propagate(False)

        steps = ("How to calibrate:\n"
                 "1. Select a field below.\n"
                 "2. Click the value of that field on the page (for example the client's name). "
                 "The box turns the field's color.\n"
                 "3. Repeat for all five fields, then press Save.\n\n"
                 "Labels next to the values are found automatically. Use the arrows to check "
                 "other pages: dashed green boxes show what the program finds there.")
        ttk.Label(left, text=steps, wraplength=290, justify="left").pack(anchor="w", pady=(0, 10))

        self.field_rows = {}
        for name, title in FIELDS.items():
            row = tk.Frame(left, bd=1, relief="groove", padx=6, pady=4)
            row.pack(fill="x", pady=3)
            tk.Label(row, bg=COLORS[name], width=2).pack(side="left", fill="y", padx=(0, 6))
            box = tk.Frame(row)
            box.pack(side="left", fill="x", expand=True)
            ttk.Radiobutton(box, text=title, value=name, variable=self.current).pack(anchor="w")
            val = ttk.Label(box, text="not set", foreground="#888", wraplength=230)
            val.pack(anchor="w")
            self.field_rows[name] = val

        self.save_btn = ttk.Button(left, text="Save calibration", command=self.save_template)
        self.save_btn.pack(fill="x", pady=(12, 4))
        ttk.Button(left, text="Clear picks", command=self.clear_picks).pack(fill="x")
        self.cal_status = ttk.Label(left, text="", wraplength=290, justify="left")
        self.cal_status.pack(anchor="w", pady=10)

        right = ttk.Frame(self.tab_cal)
        right.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)
        nav = ttk.Frame(right)
        nav.pack(fill="x")
        ttk.Button(nav, text="<  Previous", command=lambda: self.show_page(self.page_no - 1)).pack(side="left")
        self.page_lbl = ttk.Label(nav, text="Page - / -", width=16, anchor="center")
        self.page_lbl.pack(side="left", padx=8)
        ttk.Button(nav, text="Next  >", command=lambda: self.show_page(self.page_no + 1)).pack(side="left")
        self.hover_lbl = ttk.Label(nav, text="", foreground="#555")
        self.hover_lbl.pack(side="left", padx=14)

        wrap = ttk.Frame(right)
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas = tk.Canvas(wrap, bg="#e9ecf2", highlightthickness=0, cursor="crosshair")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _build_run(self):
        top = ttk.Frame(self.tab_run)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Label(top, text="Browser:").pack(side="left")
        default = "Microsoft Edge" if sys.platform == "win32" else "Built-in Chromium"
        self.browser_var = tk.StringVar(value=default)
        ttk.Combobox(top, textvariable=self.browser_var, values=list(BROWSERS), state="readonly",
                     width=18).pack(side="left", padx=6)
        self.slow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Show the browser and type slowly", variable=self.slow_var).pack(side="left", padx=12)
        self.start_btn = ttk.Button(top, text="Start", command=self.start_run)
        self.start_btn.pack(side="left", padx=(12, 4))
        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop_flag.set, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(top, text="Open reports folder", command=self.open_reports).pack(side="right")

        prog = ttk.Frame(self.tab_run)
        prog.pack(fill="x", padx=10)
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.prog_lbl = ttk.Label(prog, text="", width=34)
        self.prog_lbl.pack(side="left", padx=8)

        cols = ("page", *FIELDS, "status")
        self.table = ttk.Treeview(self.tab_run, columns=cols, show="headings", height=14)
        widths = {"page": 50, "full_name": 140, "email": 210, "phone": 140, "policy_number": 100,
                  "city": 110, "status": 260}
        for c in cols:
            self.table.heading(c, text=FIELDS.get(c, c.title()))
            self.table.column(c, width=widths[c], anchor="w", stretch=c == "status")
        self.table.tag_configure("saved", foreground="#157f3c")
        self.table.tag_configure("bad", foreground="#c4322b")
        self.table.pack(fill="both", expand=True, padx=10, pady=10)

        self.log_box = tk.Text(self.tab_run, height=8, bg="#0f172a", fg="#e2e8f0", font=("Consolas", 9),
                               relief="flat", padx=8, pady=6)
        self.log_box.pack(fill="x", padx=10, pady=(0, 10))

    # -------------------------------------------------------------- PDF pages
    def choose_pdf(self):
        path = filedialog.askopenfilename(title="Choose a PDF", filetypes=[("PDF files", "*.pdf")])
        if path:
            self.load_pdf(Path(path))

    def load_pdf(self, path: Path):
        try:
            self.pages = read_pdf(path)
            self.doc = pdfium.PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Cannot open PDF", str(exc))
            return
        self.pdf_path = path
        self.pdf_var.set(str(path))
        self.static = static_texts(self.pages)
        self.picks.clear()
        self.show_page(1)

    def show_page(self, n: int):
        if not self.pages or not 1 <= n <= len(self.pages):
            return
        self.page_no = n
        self.page_lbl.config(text=f"Page {n} / {len(self.pages)}")
        img = self.doc[n - 1].render(scale=SCALE).to_pil()
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, img.width, img.height))
        self.draw_marks()

    def _rect(self, seg, color, width=2, dash=None, tag="mark"):
        p = 3
        return self.canvas.create_rectangle(seg.x0 * SCALE - p, seg.top * SCALE - p, seg.x1 * SCALE + p,
                                            seg.bottom * SCALE + p, outline=color, width=width, dash=dash,
                                            tags=tag)

    def draw_marks(self):
        self.canvas.delete("mark")
        segments = self.pages[self.page_no - 1]
        found = {}
        if self.rules:
            tpl = Template(source_pdf="", sample_page=1, fields=list(self.rules.values()))
            found = extract_page(tpl, segments)
        for name in FIELDS:
            pick = self.picks.get(name)
            if pick and pick[0] == self.page_no:
                seg = pick[1]
                self._rect(seg, COLORS[name], width=3)
                self.canvas.create_text(seg.x1 * SCALE + 8, seg.top * SCALE - 2, text=FIELDS[name], anchor="nw",
                                        fill=COLORS[name], font=("Segoe UI", 9, "bold"), tags="mark")
            elif name in found:
                r = found[name]
                if r.label_seg:
                    self._rect(r.label_seg, "#9aa4b5", width=1, dash=(2, 2))
                if r.value_seg:
                    color = "#157f3c" if r.ok else "#c4322b"
                    self._rect(r.value_seg, color, dash=(4, 2))
                    self.canvas.create_text(r.value_seg.x1 * SCALE + 8, r.value_seg.top * SCALE - 2,
                                            text=FIELDS[name], anchor="nw", fill=color, font=("Segoe UI", 8),
                                            tags="mark")

    def _event_point(self, event):
        return self.canvas.canvasx(event.x) / SCALE, self.canvas.canvasy(event.y) / SCALE

    def on_hover(self, event):
        self.canvas.delete("hover")
        if not self.pages:
            return
        seg = segment_at(self.pages[self.page_no - 1], *self._event_point(event))
        if seg:
            self._rect(seg, COLORS[self.current.get()], width=1, tag="hover")
            self.hover_lbl.config(text=f"Click to use \"{seg.text}\" as {FIELDS[self.current.get()]}")
        else:
            self.hover_lbl.config(text="")

    def on_click(self, event):
        if not self.pages:
            return
        segments = self.pages[self.page_no - 1]
        seg = segment_at(segments, *self._event_point(event))
        if not seg:
            return
        name = self.current.get()
        try:
            rule = learn_field(name, seg, segments, self.static)
        except ValueError as exc:
            self.cal_status.config(text=f"Not used: {exc}. Click the value itself, not its label.",
                                   foreground="#c4322b")
            return
        self.rules[name] = rule
        self.picks[name] = (self.page_no, seg)
        where = "right of" if rule.direction == "left" else "under"
        self.cal_status.config(text=f"{FIELDS[name]}: found label \"{rule.label}\" ({where}), value type: {rule.kind}.",
                               foreground="#157f3c")
        names = list(FIELDS)
        missing = [n for n in names[names.index(name) + 1:] + names if n not in self.picks]
        if missing:
            self.current.set(missing[0])
        self._refresh_fields()
        self.draw_marks()

    def _refresh_fields(self):
        for name, lbl in self.field_rows.items():
            rule = self.rules.get(name)
            if not rule:
                lbl.config(text="not set", foreground="#888")
            elif name in self.picks:
                lbl.config(text=f"{rule.example}   (label: {rule.label})", foreground="#1c2433")
            else:
                lbl.config(text=f"saved: {rule.example}", foreground="#555")

    def clear_picks(self):
        self.picks.clear()
        self.rules.clear()
        self.current.set("full_name")
        self.cal_status.config(text="Cleared. Select a field and click its value on the page.", foreground="#555")
        self._refresh_fields()
        self.draw_marks()

    def save_template(self):
        missing = [FIELDS[n] for n in FIELDS if n not in self.rules]
        if missing:
            messagebox.showwarning("Not finished", "Pick these fields first:\n" + "\n".join(missing))
            return
        tpl = Template(source_pdf=self.pdf_path.name if self.pdf_path else "", sample_page=self.page_no,
                       fields=[self.rules[n] for n in FIELDS])
        tpl.save(DEFAULT_TEMPLATE)
        ok = sum(all(r.ok for r in extract_page(tpl, segs).values()) for segs in self.pages)
        self.cal_status.config(
            text=f"Saved. All five fields found on {ok} of {len(self.pages)} pages. "
                 "Use the arrows to check pages, then go to tab 2.", foreground="#157f3c")
        self.draw_marks()

    # -------------------------------------------------------------- running
    def start_run(self):
        if not self.pdf_path:
            messagebox.showwarning("No PDF", "Open a PDF first.")
            return
        if not DEFAULT_TEMPLATE.exists():
            messagebox.showwarning("No calibration", "Calibrate on tab 1 and press Save first.")
            return
        self.table.delete(*self.table.get_children())
        self.log_box.delete("1.0", "end")
        self.stop_flag.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.worker = threading.Thread(target=self._run_worker, daemon=True,
                                       args=(self.pdf_path, BROWSERS[self.browser_var.get()], self.slow_var.get()))
        self.worker.start()

    def _run_worker(self, pdf: Path, channel, slow: bool):
        try:
            tpl = Template.load(DEFAULT_TEMPLATE)
            good, skipped = extract_all(pdf, tpl)
            self.events.put(("rows", good, skipped))
            from filler import fill_records
            results = fill_records(
                good, DEFAULT_FORM.resolve().as_uri(), headed=slow, slow_mo=120 if slow else 0,
                type_delay=20 if slow else 0, channel=channel,
                on_result=lambda r: self.events.put(("result", r)), should_stop=self.stop_flag.is_set,
            )
            by_page = {r.page: r for r in results}
            rows = [[p] + [rec[f] for f in FIELDS] + [by_page[p].status if p in by_page else "not entered",
                                                       by_page[p].message if p in by_page else ""]
                    for p, rec in good]
            rows += [[p] + [rec[f] for f in FIELDS] + ["skipped", why] for p, rec, why in skipped]
            report = BASE_DIR / "output" / f"report_{datetime.now():%Y%m%d_%H%M%S}.csv"
            write_csv(report, ["page", *FIELDS, "status", "note"], sorted(rows, key=lambda r: r[0]))
            saved = sum(r.status == "saved" for r in results)
            log.info("Finished: %d saved, %d rejected, %d skipped. Report: %s",
                     saved, len(results) - saved, len(skipped), report.name)
            self.events.put(("done", report))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            if "Executable doesn't exist" in str(exc) or "is not found" in str(exc):
                msg += "\n\nChoose another browser in the list (for example Microsoft Edge)."
            self.events.put(("error", msg))

    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log_box.insert("end", ev[1] + "\n")
                    self.log_box.see("end")
                elif kind == "rows":
                    good, skipped = ev[1], ev[2]
                    items = [(p, rec, "ready", "") for p, rec in good] + \
                            [(p, rec, "skipped: " + why, "bad") for p, rec, why in skipped]
                    for p, rec, status, tag in sorted(items, key=lambda t: t[0]):
                        self.table.insert("", "end", iid=str(p), values=(p, *[rec[f] for f in FIELDS], status),
                                          tags=(tag,) if tag else ())
                    self.done_count = 0
                    self.progress.config(maximum=max(1, len(good)), value=0)
                    self.prog_lbl.config(text=f"0 / {len(good)} entered")
                elif kind == "result":
                    r = ev[1]
                    status = "saved" if r.status == "saved" else f"{r.status}: {r.message}"
                    vals = list(self.table.item(str(r.page), "values"))
                    vals[-1] = status
                    self.table.item(str(r.page), values=vals, tags=("saved" if r.status == "saved" else "bad",))
                    self.table.see(str(r.page))
                    self.done_count += 1
                    total = int(self.progress.cget("maximum"))
                    self.progress.config(value=self.done_count)
                    self.prog_lbl.config(text=f"{self.done_count} / {total} entered")
                elif kind in ("done", "error"):
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    if kind == "done":
                        self.last_report = ev[1]
                        self.prog_lbl.config(text=self.prog_lbl.cget("text") + "  - done")
                    else:
                        messagebox.showerror("Run failed", ev[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def open_reports(self):
        folder = BASE_DIR / "output"
        folder.mkdir(exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


if __name__ == "__main__":
    App().mainloop()
