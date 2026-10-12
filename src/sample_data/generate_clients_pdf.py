"""Generate sample_data/clients.pdf: one fictional client intake form per page.

Each page has about 40 labelled values, but the demo CRM form needs only five:
full name, email, phone, policy number and city. The layout is not identical
from page to page (optional banner, a "Previous policies" table with 0-3 rows,
notes of different length), so the five values move up and down the page.
Page 7 has no phone number and page 13 repeats the client from page 3, so the
run also shows how incomplete and duplicate records are handled.
There are also look-alike values (agent email/phone, emergency contact phone
and email) that a naive "first email on the page" approach would pick up.

All data is fictional: names are made up, emails use example.com and phone
numbers use the 555-01xx range reserved for fiction.

    python sample_data/generate_clients_pdf.py
"""

from __future__ import annotations

import random
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent / "clients.pdf"
SEED = 7

NAVY = HexColor("#0f1d3a")
GREY = HexColor("#6b7280")
LINE = HexColor("#cfd5e1")
TINT = HexColor("#f2f5fa")

CLIENTS = [
    # full name, email, phone, policy, city, street, zip, state
    ("Olivia Bennett", "olivia.bennett@example.com", "+1 (555) 010-2231", "HL-482137", "Portland", "1427 Larkspur Ave", "97205", "OR"),
    ("Marcus Delgado", "m.delgado@example.com", "+1 (555) 010-4478", "HL-482152", "San Antonio", "88 Juniper Ct", "78205", "TX"),
    ("Hannah Kowalski", "hannah.k@example.com", "+1 (555) 010-9013", "HL-482166", "Milwaukee", "301 Birchwood Rd", "53202", "WI"),
    ("Daniel Okafor", "d.okafor@example.com", "+1 (555) 010-3356", "HL-482171", "Columbus", "19 Maple Grove Ln", "43215", "OH"),
    ("Sophie Laurent", "sophie.laurent@example.com", "+1 (555) 010-6620", "HL-482189", "New Orleans", "740 Magnolia St", "70112", "LA"),
    ("Ethan Brooks", "ethan.brooks@example.com", "+1 (555) 010-1184", "HL-482194", "Denver", "52 Aspen Ridge Dr", "80202", "CO"),
    ("Aisha Rahman", "aisha.rahman@example.com", "", "HL-482208", "Minneapolis", "6 Lakeview Terrace", "55401", "MN"),
    ("Lucas Ferreira", "lucas.ferreira@example.com", "+1 (555) 010-7742", "HL-482213", "Salt Lake City", "915 Canyon View Rd", "84101", "UT"),
    ("Grace Whitfield", "grace.w@example.com", "+1 (555) 010-5509", "HL-482227", "Raleigh", "27 Dogwood Path", "27601", "NC"),
    ("Noah Lindqvist", "noah.lindqvist@example.com", "+1 (555) 010-8865", "HL-482231", "Boise", "410 Sagebrush Way", "83702", "ID"),
    ("Isabel Moreno", "isabel.moreno@example.com", "+1 (555) 010-2097", "HL-482245", "Tucson", "1180 Saguaro Blvd", "85701", "AZ"),
    ("Kenji Watanabe", "kenji.w@example.com", "+1 (555) 010-3418", "HL-482250", "Sacramento", "63 Riverbend Ct", "95814", "CA"),
    # Same client as page 3 sent twice: the CRM form rejects the duplicate policy.
    ("Hannah Kowalski", "hannah.k@example.com", "+1 (555) 010-9013", "HL-482166", "Milwaukee", "301 Birchwood Rd", "53202", "WI"),
]

AGENTS = [
    ("Rachel Stone", "r.stone@harborline.example.com", "+1 (555) 010-0100"),
    ("Tom Alvarez", "t.alvarez@harborline.example.com", "+1 (555) 010-0145"),
    ("Priya Nair", "p.nair@harborline.example.com", "+1 (555) 010-0172"),
]
FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Sarah", "David", "Emma", "Chris", "Nina"]
RELATION = ["Spouse", "Sibling", "Parent", "Friend", "Partner"]
PRODUCTS = ["Home Plus", "Home Basic", "Auto Comprehensive", "Renters Standard", "Life Term 20"]
PAYMENT = ["Direct debit", "Card", "Bank transfer"]
NOTES = [
    "Client requested paper copies of all policy documents.",
    "Prefers contact by email between 9:00 and 17:00.",
    "Discount applied for bundled home and auto cover.",
    "Previous insurer confirmed no open claims.",
    "Annual review scheduled with the assigned agent.",
    "Proof of address received and verified by the branch.",
]


def label_value(c, x, y, label, value, value_x):
    """Label on the left, value on the same line further right."""
    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)
    c.drawString(x, y, label)
    c.setFont("Helvetica", 10)
    c.setFillColor(NAVY)
    c.drawString(value_x, y, value)


def box(c, x, y, w, label, value):
    """Boxed field: small label at the top, value underneath."""
    c.setStrokeColor(LINE)
    c.setFillColor(TINT)
    c.roundRect(x, y - 30, w, 34, 3, stroke=1, fill=1)
    c.setFont("Helvetica", 7)
    c.setFillColor(GREY)
    c.drawString(x + 6, y - 6, label.upper())
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(NAVY)
    c.drawString(x + 6, y - 22, value)


def section(c, y, title):
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(NAVY)
    c.drawString(50, y, title)
    c.setStrokeColor(LINE)
    c.line(50, y - 5, 545, y - 5)
    return y - 24


def draw_page(c, n, total, client, rnd):
    name, email, phone, policy, city, street, zipcode, state = client
    agent = rnd.choice(AGENTS)
    width, height = A4
    y = height - 50

    # Header
    c.setFillColor(NAVY)
    c.rect(0, height - 80, width, 80, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 42, "Harborline Insurance")
    c.setFont("Helvetica", 9)
    c.drawString(50, height - 58, "Client Intake Form  -  Branch 014, 200 Harbor St, Springfield")
    c.drawRightString(545, height - 42, f"Form ID: CIF-2026-{4100 + n * 7}")
    c.drawRightString(545, height - 58, f"Issued: 2026-0{rnd.randint(1, 9)}-{rnd.randint(10, 28)}")
    y = height - 104

    if rnd.random() < 0.4:  # optional banner pushes everything down
        c.setFillColor(HexColor("#fff4d6"))
        c.rect(50, y - 8, 495, 22, stroke=0, fill=1)
        c.setFillColor(HexColor("#8a5a00"))
        c.setFont("Helvetica-Bold", 9)
        c.drawString(58, y, "REISSUED COPY - replaces the form sent earlier this year")
        y -= 34

    label_value(c, 50, y, "Agent", agent[0], 120)
    label_value(c, 310, y, "Agent phone", agent[2], 390)
    y -= 16
    label_value(c, 50, y, "Agent email", agent[1], 120)
    label_value(c, 310, y, "Agent code", f"AG-{rnd.randint(100, 999)}", 390)
    y -= 30

    # Policyholder: label-left layout
    y = section(c, y, "1. Policyholder")
    dob = f"19{rnd.randint(55, 99)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
    rows = [
        ("Full name", name, "Date of birth", dob),
        ("Email", email, "Phone", phone),
        ("Secondary phone", "-", "Preferred contact", rnd.choice(["Email", "Phone", "Post"])),
        ("Street address", street, "Apartment / unit", rnd.choice(["-", "Apt 2B", "Unit 5", "-"])),
        ("City", city, "State", state),
        ("ZIP code", zipcode, "Country", "United States"),
        ("Occupation", rnd.choice(["Teacher", "Engineer", "Nurse", "Designer", "Accountant", "Chef"]),
         "Customer since", str(rnd.randint(2008, 2025))),
    ]
    for l1, v1, l2, v2 in rows:
        label_value(c, 50, y, l1, v1, 145)
        label_value(c, 310, y, l2, v2, 405)
        y -= 18
    y -= 8

    # Previous policies: 0-3 rows, shifts the next sections
    prev = rnd.randint(0, 3)
    if prev:
        y = section(c, y, "Previous policies")
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GREY)
        for x, h in [(50, "Policy"), (150, "Product"), (300, "From"), (380, "To"), (460, "Claims")]:
            c.drawString(x, y, h)
        y -= 14
        c.setFont("Helvetica", 9)
        c.setFillColor(NAVY)
        for _ in range(prev):
            start = rnd.randint(2012, 2021)
            for x, v in [(50, f"OLD-{rnd.randint(10000, 99999)}"), (150, rnd.choice(PRODUCTS)),
                         (300, str(start)), (380, str(start + rnd.randint(1, 4))), (460, str(rnd.randint(0, 2)))]:
                c.drawString(x, y, v)
            y -= 13
        y -= 10

    # Policy: boxed layout (label above value)
    y = section(c, y, "2. Policy")
    boxes = [
        ("Policy number", policy), ("Product", rnd.choice(PRODUCTS)), ("Start date", f"2026-{rnd.randint(1, 12):02d}-01"),
        ("Term", rnd.choice(["12 months", "24 months", "36 months"])), ("Annual premium", f"${rnd.randint(380, 2400)}.00"),
        ("Sum insured", f"${rnd.randint(50, 900) * 1000:,}"),
        ("Payment method", rnd.choice(PAYMENT)), ("Account (masked)", f"XXXX-XXXX-{rnd.randint(1000, 9999)}"),
        ("Deductible", f"${rnd.choice([250, 500, 1000])}"),
    ]
    for i, (lbl, val) in enumerate(boxes):
        col, row = i % 3, i // 3
        box(c, 50 + col * 168, y - row * 42, 159, lbl, val)
    y -= 3 * 42 + 14

    # Emergency contact: look-alike phone and email
    y = section(c, y, "3. Emergency contact")
    first = rnd.choice(FIRST)
    last = name.split()[-1]
    label_value(c, 50, y, "Contact name", f"{first} {last}", 145)
    label_value(c, 310, y, "Relationship", rnd.choice(RELATION), 405)
    y -= 18
    label_value(c, 50, y, "Contact phone", f"+1 (555) 010-{rnd.randint(1000, 9999)}", 145)
    label_value(c, 310, y, "Contact email", f"{first.lower()}.{last.lower()}@example.com", 405)
    y -= 26

    # Notes: 1-4 lines
    y = section(c, y, "4. Notes")
    c.setFont("Helvetica", 9)
    c.setFillColor(NAVY)
    for note in rnd.sample(NOTES, rnd.randint(1, 4)):
        c.drawString(50, y, "- " + note)
        y -= 13
    y -= 12

    # Declaration and signature
    y = section(c, y, "5. Declaration")
    c.setFont("Helvetica", 8)
    c.setFillColor(GREY)
    for line in [
        "I confirm that the information above is true and complete to the best of my knowledge.",
        "I agree that Harborline Insurance may process this data to administer my policy.",
    ]:
        c.drawString(50, y, line)
        y -= 12
    y -= 18
    c.setStrokeColor(NAVY)
    c.line(50, y, 230, y)
    c.line(310, y, 450, y)
    c.drawString(50, y - 11, "Signature of policyholder")
    c.drawString(310, y - 11, "Date")

    c.setFont("Helvetica", 7)
    c.drawCentredString(width / 2, 18, f"Harborline Insurance - fictional demo document - page {n} of {total}")


def main() -> None:
    rnd = random.Random(SEED)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle("Client intake forms (fictional demo data)")
    for i, client in enumerate(CLIENTS, start=1):
        draw_page(c, i, len(CLIENTS), client, rnd)
        c.showPage()
    c.save()
    print(f"Wrote {OUT} ({len(CLIENTS)} pages)")


if __name__ == "__main__":
    main()
