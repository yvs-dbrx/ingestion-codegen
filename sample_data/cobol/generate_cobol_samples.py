"""Generates realistic mainframe-style test data: CUSTOMER, PRODUCT and ORDERS files in
EBCDIC (code page 037), fixed-length records, plus their COBOL copybooks.

    python generate_cobol_samples.py

What makes these "true" mainframe files rather than ASCII text with a copybook:
  * characters are EBCDIC (cp037) — "A" is 0xC1, not 0x41, digits are 0xF0-0xF9
  * COMP-3 packed decimals (two digits per byte, sign in the last nibble)
  * COMP binary integers (big-endian two's complement, 2/4/8 bytes by digit count)
  * signed zoned decimals with a trailing overpunch sign (0xC_ = +, 0xD_ = -)
  * no line endings and no delimiters: every record is exactly RECLEN bytes

The copybooks are produced from the same field specs the data is encoded from, so the two can
never disagree. After writing, verify() re-reads each file using ONLY the copybook text (its own
small parser, independent of the encoder) and compares every value against expected/*.csv.

Deterministic: the same seed always produces byte-identical files.
"""
import csv
import random
import re
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

OUT = Path(__file__).parent
CODEPAGE = "cp037"
SEED = 20261013


# --- field model -----------------------------------------------------------------------

@dataclass
class Fld:
    level: int
    name: str
    kind: str  # 'X' alphanumeric | '9' numeric
    digits: int  # characters for X; total digits (integer + fraction) for 9
    scale: int = 0
    signed: bool = False
    usage: str = "DISPLAY"  # DISPLAY | COMP-3 | COMP
    note: str = ""

    @property
    def length(self) -> int:
        if self.kind == "X":
            return self.digits
        if self.usage == "COMP-3":
            return self.digits // 2 + 1
        if self.usage == "COMP":
            return 2 if self.digits <= 4 else 4 if self.digits <= 9 else 8
        return self.digits

    @property
    def pic(self) -> str:
        def run(ch: str, n: int) -> str:
            return ch if n == 1 else ch * n if n == 2 else f"{ch}({n})"

        if self.kind == "X":
            return run("X", self.digits)
        whole = self.digits - self.scale
        text = ("S" if self.signed else "") + run("9", whole)
        if self.scale:
            text += "V" + run("9", self.scale)
        return text

    @property
    def clause(self) -> str:
        return f"PIC {self.pic}" + ("" if self.usage == "DISPLAY" else f" {self.usage}")


@dataclass
class Grp:
    level: int
    name: str
    children: list = field(default_factory=list)


def X(level, name, n, note=""):
    return Fld(level, name, "X", n, note=note)


def N(level, name, digits, scale=0, signed=False, usage="DISPLAY", note=""):
    return Fld(level, name, "9", digits, scale, signed, usage, note)


def leaves(node, path=()):
    """Yield (flattened_name, Fld) for every elementary item, in record order."""
    for child in node.children:
        if isinstance(child, Grp):
            yield from leaves(child, path + (child.name,))
        else:
            yield "_".join(path + (child.name,)).replace("-", "_"), child


def record_length(root: Grp) -> int:
    return sum(f.length for _, f in leaves(root))


# --- encoders --------------------------------------------------------------------------

def enc_text(f: Fld, value) -> bytes:
    s = "" if value is None else str(value)
    if len(s) > f.digits:
        raise ValueError(f"{f.name}: {s!r} is longer than {f.digits}")
    return s.ljust(f.digits).encode(CODEPAGE)


def scaled_int(f: Fld, value) -> int:
    d = Decimal(value).scaleb(f.scale)
    if d != d.to_integral_value():
        raise ValueError(f"{f.name}: {value} has more than {f.scale} decimals")
    n = int(d)
    if abs(n) >= 10**f.digits:
        raise ValueError(f"{f.name}: {value} doesn't fit {f.pic}")
    if n < 0 and not f.signed:
        raise ValueError(f"{f.name}: negative value for an unsigned field")
    return n


def enc_zoned(f: Fld, value) -> bytes:
    n = scaled_int(f, value)
    digits = str(abs(n)).zfill(f.digits)
    out = bytearray(0xF0 | int(c) for c in digits)
    if f.signed:  # trailing overpunch: zone C = positive, D = negative
        out[-1] = (0xD0 if n < 0 else 0xC0) | (out[-1] & 0x0F)
    return bytes(out)


def enc_packed(f: Fld, value) -> bytes:
    n = scaled_int(f, value)
    nibbles = [int(c) for c in str(abs(n)).zfill(f.digits)]
    nibbles.append((0xD if n < 0 else 0xC) if f.signed else 0xF)
    if len(nibbles) % 2:
        nibbles.insert(0, 0)
    return bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def enc_binary(f: Fld, value) -> bytes:
    return scaled_int(f, value).to_bytes(f.length, "big", signed=f.signed)


def encode_field(f: Fld, value) -> bytes:
    if f.kind == "X":
        return enc_text(f, value)
    if f.usage == "COMP-3":
        return enc_packed(f, value)
    if f.usage == "COMP":
        return enc_binary(f, value)
    return enc_zoned(f, value)


def encode_record(root: Grp, row: dict) -> bytes:
    data = b"".join(encode_field(f, row.get(f.name, "" if f.kind == "X" else 0)) for _, f in leaves(root))
    assert len(data) == record_length(root)
    return data


# --- copybook writer -------------------------------------------------------------------

def banner(title: str, purpose: str, root: Grp) -> list[str]:
    width = 72
    rule = "      *" + "=" * (width - 8) + "*"

    def line(text: str = "") -> str:
        return ("      * " + text).ljust(width - 1) + "*"

    out = [rule, line(f"COPYBOOK : {title}")]
    for i, chunk in enumerate(textwrap.wrap(purpose, 52)):
        out.append(line(("PURPOSE  : " if i == 0 else "           ") + chunk))
    out += [line(f"RECLEN   : {record_length(root)} bytes, fixed length, EBCDIC ({CODEPAGE})"), line(),
            line("FIELD MAP (offset is 1-based):")]
    pos = 1
    for flat, f in leaves(root):
        kind = f.usage if f.usage != "DISPLAY" else ("zoned" if f.kind == "9" else "text")
        out.append(line(f"{pos:>4}-{pos + f.length - 1:<4} {f.name:<20} {f.pic:<12} {kind}"))
        pos += f.length
    out += [line()]
    return out


def write_copybook(path: Path, title: str, purpose: str, root: Grp, extra: list[str] = ()) -> None:
    lines = banner(title, purpose, root)
    for e in extra:
        for chunk in textwrap.wrap(e, 60, subsequent_indent="  "):
            lines.append(("      * " + chunk).ljust(71) + "*")
    lines.append("      *" + "=" * 64 + "*")

    def emit(node, indent):
        for child in node.children:
            pad = " " * 7 + " " * indent
            if isinstance(child, Grp):
                lines.append(f"{pad}{child.level:02d}  {child.name}.")
                emit(child, indent + 4)
            else:
                lines.append(f"{pad}{child.level:02d}  {child.name:<22}{child.clause}.")

    lines.append(f"       {root.level:02d}  {root.name}.")
    emit(root, 4)
    assert all(len(l) <= 72 for l in lines), [l for l in lines if len(l) > 72]
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


# --- record layouts --------------------------------------------------------------------

CUSTOMER = Grp(1, "CUSTOMER-RECORD", [
    N(5, "CUST-ID", 8),
    Grp(5, "CUST-NAME", [X(10, "CUST-FIRST-NAME", 15), X(10, "CUST-MIDDLE-INIT", 1), X(10, "CUST-LAST-NAME", 20)]),
    Grp(5, "CUST-ADDRESS", [
        X(10, "CUST-STREET-LINE1", 30), X(10, "CUST-STREET-LINE2", 30), X(10, "CUST-CITY", 20),
        X(10, "CUST-STATE-CODE", 2), X(10, "CUST-ZIP-CODE", 10), X(10, "CUST-COUNTRY-CODE", 3)]),
    Grp(5, "CUST-CONTACT", [X(10, "CUST-PHONE", 15), X(10, "CUST-EMAIL", 40)]),
    X(5, "CUST-TYPE", 1),
    X(5, "CUST-STATUS", 1),
    N(5, "CUST-DATE-ADDED", 8),
    N(5, "CUST-DATE-MODIFIED", 8),
    Grp(5, "CUST-FINANCIAL", [
        N(10, "CUST-CREDIT-LIMIT", 9, 2, True, "COMP-3"),
        N(10, "CUST-BALANCE", 11, 2, True, "COMP-3"),
        N(10, "CUST-YTD-RETURNS", 9, 2, True, "DISPLAY"),
        N(10, "CUST-LOYALTY-POINTS", 9, 0, True, "COMP"),
        N(10, "CUST-ORDER-COUNT", 4, 0, False, "COMP")]),
    X(5, "FILLER", 10),
])

PRODUCT = Grp(1, "PRODUCT-RECORD", [
    N(5, "PROD-ID", 8),
    X(5, "PROD-NAME", 40),
    X(5, "PROD-DESCRIPTION", 60),
    Grp(5, "PROD-CATEGORY", [X(10, "PROD-CAT-CODE", 4), X(10, "PROD-CAT-DESC", 25)]),
    Grp(5, "PROD-SUBCATEGORY", [X(10, "PROD-SUBCAT-CODE", 5), X(10, "PROD-SUBCAT-DESC", 25)]),
    Grp(5, "PROD-PRICING", [
        N(10, "PROD-UNIT-PRICE", 9, 2, True, "COMP-3"),
        N(10, "PROD-UNIT-COST", 9, 2, True, "COMP-3"),
        N(10, "PROD-TAX-RATE", 5, 4, True, "COMP-3")]),
    Grp(5, "PROD-INVENTORY", [
        N(10, "PROD-QTY-ON-HAND", 7, 0, True, "COMP"),
        N(10, "PROD-REORDER-LVL", 4, 0, True, "COMP"),
        N(10, "PROD-WEIGHT-KG", 8, 3, True, "COMP-3")]),
    X(5, "PROD-UOM", 5),
    X(5, "PROD-STATUS", 1),
    N(5, "PROD-DATE-ADDED", 8),
    N(5, "PROD-DATE-MODIFIED", 8),
    X(5, "FILLER", 20),
])

ORDERS = Grp(1, "ORDER-RECORD", [
    N(5, "ORD-ID", 10),
    N(5, "ORD-CUST-ID", 8),
    N(5, "ORD-PROD-ID", 8),
    Grp(5, "ORD-DATES", [N(10, "ORD-ORDER-DATE", 8), N(10, "ORD-SHIP-DATE", 8), N(10, "ORD-DELIVERY-DATE", 8)]),
    X(5, "ORD-STATUS", 1),
    X(5, "ORD-PAYMENT-METHOD", 2),
    Grp(5, "ORD-SHIP-TO", [X(10, "ORD-SHIP-CITY", 20), X(10, "ORD-SHIP-STATE", 2), X(10, "ORD-SHIP-ZIP", 10)]),
    Grp(5, "ORD-AMOUNTS", [
        N(10, "ORD-QUANTITY", 5, 0, True, "COMP"),
        N(10, "ORD-UNIT-PRICE", 9, 2, True, "COMP-3"),
        N(10, "ORD-DISCOUNT", 9, 2, True, "COMP-3"),
        N(10, "ORD-TAX", 9, 2, True, "COMP-3"),
        N(10, "ORD-SHIPPING", 7, 2, True, "COMP-3"),
        N(10, "ORD-TOTAL", 11, 2, True, "COMP-3")]),
    X(5, "ORD-SALES-REP", 6),
    X(5, "FILLER", 10),
])


# --- sample data -----------------------------------------------------------------------

FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Patricia", "William", "Jennifer", "David", "Elizabeth",
         "Richard", "Barbara", "Joseph", "Susan", "Thomas", "Jessica", "Charles", "Sarah", "Christopher", "Karen",
         "Daniel", "Nancy", "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Margaret", "Donald", "Sandra"]
LAST = ["Harrington", "Delgado-Reyes", "O'Brien", "Nakamura", "Fitzgerald", "Okafor", "Johansson", "Petrov", "Chen",
        "Williams", "Garcia", "Martinez", "Anderson", "Thompson", "Robinson", "Clark", "Lewis", "Walker", "Hall",
        "Allen", "Young", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams"]
PLACES = [("Austin", "TX", "78701"), ("Denver", "CO", "80202"), ("Seattle", "WA", "98101"), ("Boston", "MA", "02108"),
          ("Chicago", "IL", "60601"), ("Atlanta", "GA", "30303"), ("Phoenix", "AZ", "85004"),
          ("Portland", "OR", "97204"), ("Miami", "FL", "33101"), ("Nashville", "TN", "37201"),
          ("Detroit", "MI", "48226"), ("Minneapolis", "MN", "55401")]
STREETS = ["Meadowlark Dr", "Cypress Creek Blvd", "Oak Ridge Ln", "Maple Ave", "Lakeview Ct", "Sunset Blvd",
           "Highland Pkwy", "River Rd"]

ELEC, CLTH, HOME, FOOD = ("ELEC", "Electronics"), ("CLTH", "Clothing & Apparel"), ("HOME", "Home & Garden"), ("FOOD", "Food & Grocery")
# name, description, category, (sub code, sub desc), price, cost, weight kg, uom, tax rate
PRODUCTS = [
    ("Smartphone X12 128GB", "6.1 inch OLED display, dual camera, 5G unlocked", ELEC, ("MOBI", "Mobile Devices"), "799.00", "520.00", "0.174", "EACH", "0.0825"),
    ("Wireless Earbuds Pro", "Active noise cancelling, 24 hour battery case", ELEC, ("MOBI", "Mobile Devices"), "159.00", "74.50", "0.056", "EACH", "0.0825"),
    ("UltraBook 14 Laptop", "14 inch, 16GB memory, 512GB solid state drive", ELEC, ("LAPT", "Laptops & Computers"), "1299.00", "910.00", "1.320", "EACH", "0.0825"),
    ("Gaming Laptop 16", "16 inch, discrete graphics, 165Hz display", ELEC, ("LAPT", "Laptops & Computers"), "1899.00", "1380.00", "2.450", "EACH", "0.0825"),
    ("55in 4K Smart TV", "55 inch 4K HDR, built in streaming apps", ELEC, ("TVHF", "TV & Home Theater"), "549.99", "380.00", "13.800", "EACH", "0.0825"),
    ("Soundbar 2.1 Channel", "Wireless subwoofer, HDMI ARC, Bluetooth", ELEC, ("TVHF", "TV & Home Theater"), "179.99", "105.00", "3.600", "EACH", "0.0825"),
    ("Mens Classic Oxford Shirt", "100 percent cotton, button down collar", CLTH, ("MENSW", "Men's Clothing"), "49.50", "21.00", "0.280", "EACH", "0.0625"),
    ("Mens Slim Fit Chinos", "Stretch cotton twill, five pocket styling", CLTH, ("MENSW", "Men's Clothing"), "64.00", "28.00", "0.520", "EACH", "0.0625"),
    ("Womens Wool Blend Coat", "Double breasted, fully lined, knee length", CLTH, ("WMNSW", "Women's Clothing"), "189.00", "82.00", "1.150", "EACH", "0.0625"),
    ("Womens Running Shoes", "Lightweight mesh upper, cushioned midsole", CLTH, ("WMNSW", "Women's Clothing"), "94.95", "41.00", "0.620", "PAIR", "0.0625"),
    ("Kids Rain Jacket", "Waterproof shell with hood, reflective trim", CLTH, ("KIDSC", "Kids' Clothing"), "39.99", "16.50", "0.340", "EACH", "0.0625"),
    ("Kids Backpack 20L", "Padded straps, water bottle pocket", CLTH, ("KIDSC", "Kids' Clothing"), "29.95", "12.00", "0.480", "EACH", "0.0625"),
    ("Oak Dining Table 6 Seat", "Solid oak, extends to eight seats", HOME, ("FRNTR", "Furniture"), "899.00", "510.00", "42.000", "EACH", "0.0825"),
    ("Ergonomic Office Chair", "Mesh back, adjustable lumbar and arms", HOME, ("FRNTR", "Furniture"), "349.00", "180.00", "16.500", "EACH", "0.0825"),
    ("Stainless Cookware Set", "10 piece, induction compatible, oven safe", HOME, ("KITCH", "Kitchen & Dining"), "249.00", "121.00", "9.800", "SET", "0.0825"),
    ("Programmable Coffee Maker", "12 cup, thermal carafe, brew timer", HOME, ("KITCH", "Kitchen & Dining"), "89.99", "42.00", "2.700", "EACH", "0.0825"),
    ("Cordless Leaf Blower", "40V battery included, variable speed", HOME, ("GRDNR", "Garden & Outdoor"), "159.00", "78.00", "2.900", "EACH", "0.0825"),
    ("Patio Umbrella 9ft", "Tilt mechanism, UV resistant canopy", HOME, ("GRDNR", "Garden & Outdoor"), "119.00", "57.00", "6.400", "EACH", "0.0825"),
    ("Organic Granola 12oz", "Honey almond, no added preservatives", FOOD, ("BKFST", "Breakfast & Cereal"), "6.49", "3.10", "0.340", "EACH", "0.0000"),
    ("Steel Cut Oats 24oz", "Whole grain, quick cooking", FOOD, ("BKFST", "Breakfast & Cereal"), "5.29", "2.40", "0.680", "EACH", "0.0000"),
    ("Sea Salt Kettle Chips 8oz", "Kettle cooked, gluten free", FOOD, ("SNACK", "Snacks & Chips"), "3.99", "1.70", "0.227", "EACH", "0.0000"),
    ("Mixed Nut Trail Mix 16oz", "Roasted nuts, dried fruit, dark chocolate", FOOD, ("SNACK", "Snacks & Chips"), "8.99", "4.60", "0.454", "EACH", "0.0000"),
    ("Cold Brew Coffee 32oz", "Ready to drink, smooth and low acid", FOOD, ("BEVER", "Beverages"), "7.49", "3.30", "0.950", "EACH", "0.0000"),
    ("Sparkling Water 12 Pack", "Lime flavor, zero calories", FOOD, ("BEVER", "Beverages"), "5.99", "2.50", "4.300", "CASE", "0.0000"),
    ("Family Cereal Variety Box", "Assorted single serve cups", FOOD, ("BKFST", "Breakfast & Cereal"), "11.99", "5.60", "1.100", "BOX", "0.0000"),
]

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def ymd(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def build_products(rnd: random.Random) -> list[dict]:
    rows = []
    for i, (name, desc, cat, sub, price, cost, kg, uom, tax) in enumerate(PRODUCTS):
        added = date(2019, 1, 15) + timedelta(days=rnd.randint(0, 2400))
        rows.append({
            "PROD-ID": 10000001 + i, "PROD-NAME": name, "PROD-DESCRIPTION": desc,
            "PROD-CAT-CODE": cat[0], "PROD-CAT-DESC": cat[1], "PROD-SUBCAT-CODE": sub[0], "PROD-SUBCAT-DESC": sub[1],
            "PROD-UNIT-PRICE": Decimal(price), "PROD-UNIT-COST": Decimal(cost), "PROD-TAX-RATE": Decimal(tax),
            "PROD-QTY-ON-HAND": rnd.randint(0, 5000), "PROD-REORDER-LVL": rnd.randint(10, 500),
            "PROD-WEIGHT-KG": Decimal(kg), "PROD-UOM": uom, "PROD-STATUS": "A",
            "PROD-DATE-ADDED": ymd(added), "PROD-DATE-MODIFIED": ymd(added + timedelta(days=rnd.randint(0, 900))),
        })
    # deliberate edge cases
    rows[9]["PROD-QTY-ON-HAND"] = -12            # backordered: negative binary value
    rows[17]["PROD-STATUS"] = "S"                # seasonal
    rows[16]["PROD-STATUS"] = "S"
    rows[3]["PROD-STATUS"] = "D"                 # discontinued, nothing left
    rows[3]["PROD-QTY-ON-HAND"] = 0
    return rows


def build_orders(rnd: random.Random, customers: list[dict], products: list[dict]) -> list[dict]:
    by_prod = {p["PROD-ID"]: p for p in products}
    by_cust = {c["CUST-ID"]: c for c in customers}
    rows = []
    for k in range(60):
        cust = rnd.choice(customers)
        prod = rnd.choice(products)
        qty = rnd.choice([1, 1, 1, 2, 2, 3, 4, 6, 12])
        ordered = date(2026, 1, 3) + timedelta(days=rnd.randint(0, 270))
        status = rnd.choices("DSPCR", weights=[45, 20, 15, 10, 10])[0]
        ship = deliv = 0
        if status in "SDR":
            shipped = ordered + timedelta(days=rnd.randint(1, 3))
            ship = ymd(shipped)
        if status in "DR":
            deliv = ymd(shipped + timedelta(days=rnd.randint(2, 6)))
        price = prod["PROD-UNIT-PRICE"]
        subtotal = money(price * qty)
        discount = money(subtotal * rnd.choice([Decimal("0"), Decimal("0"), Decimal("0.05"), Decimal("0.10")]))
        tax = money((subtotal - discount) * prod["PROD-TAX-RATE"])
        shipping = Decimal("0.00") if subtotal - discount >= 100 else rnd.choice([Decimal("7.99"), Decimal("12.50")])
        total = subtotal - discount + tax + shipping
        if status == "C":                         # cancelled: nothing charged
            discount = tax = shipping = total = Decimal("0.00")
        rows.append({
            "ORD-ID": 5000000001 + k, "ORD-CUST-ID": cust["CUST-ID"], "ORD-PROD-ID": prod["PROD-ID"],
            "ORD-ORDER-DATE": ymd(ordered), "ORD-SHIP-DATE": ship, "ORD-DELIVERY-DATE": deliv,
            "ORD-STATUS": status, "ORD-PAYMENT-METHOD": rnd.choice(["CC", "CC", "CC", "DC", "PP", "WT", "CK"]),
            "ORD-SHIP-CITY": cust["CUST-CITY"], "ORD-SHIP-STATE": cust["CUST-STATE-CODE"], "ORD-SHIP-ZIP": cust["CUST-ZIP-CODE"],
            "ORD-QUANTITY": qty, "ORD-UNIT-PRICE": price, "ORD-DISCOUNT": discount, "ORD-TAX": tax,
            "ORD-SHIPPING": shipping, "ORD-TOTAL": total, "ORD-SALES-REP": f"REP{rnd.randint(1, 12):03d}",
        })
    for c in customers:  # keep customer.order_count consistent with the orders file
        c["CUST-ORDER-COUNT"] = sum(1 for o in rows if o["ORD-CUST-ID"] == c["CUST-ID"])
    del by_cust
    return rows


def build_customers(rnd: random.Random) -> list[dict]:
    lasts = LAST[:]
    rnd.shuffle(lasts)
    rows = []
    for i in range(30):
        city, state, zipc = PLACES[rnd.randrange(len(PLACES))]
        first, last = FIRST[i], lasts[i]
        added = date(2018, 1, 1) + timedelta(days=rnd.randint(0, 2900))
        limit = Decimal(rnd.choice([0, 2500, 5000, 10000, 25000])).quantize(CENT)
        rows.append({
            "CUST-ID": 100001 + i, "CUST-FIRST-NAME": first,
            "CUST-MIDDLE-INIT": rnd.choice("ABCDEFGHJKLMNPRST"),
            "CUST-LAST-NAME": last,
            "CUST-STREET-LINE1": f"{rnd.randint(100, 9999)} {rnd.choice(STREETS)}",
            "CUST-STREET-LINE2": rnd.choice(["", "", f"Apt {rnd.randint(1, 40)}{rnd.choice('ABC')}", f"Suite {rnd.randint(100, 900)}"]),
            "CUST-CITY": city, "CUST-STATE-CODE": state, "CUST-ZIP-CODE": zipc, "CUST-COUNTRY-CODE": "USA",
            "CUST-PHONE": f"+1-555-555-{rnd.randint(0, 9999):04d}",
            "CUST-EMAIL": f"{first}.{last}@email.com".lower().replace("'", ""),
            "CUST-TYPE": rnd.choices("RWPC", weights=[60, 10, 20, 10])[0],
            "CUST-STATUS": rnd.choices("AIS", weights=[80, 12, 8])[0],
            "CUST-DATE-ADDED": ymd(added),
            "CUST-DATE-MODIFIED": ymd(min(added + timedelta(days=rnd.randint(0, 1500)), date(2026, 10, 1))),
            "CUST-CREDIT-LIMIT": limit,
            "CUST-BALANCE": money(Decimal(rnd.uniform(0, float(limit) or 1)) * Decimal("0.6")),
            "CUST-YTD-RETURNS": money(Decimal(rnd.choice([0, 0, 0, 1])) * Decimal(rnd.uniform(5, 450))),
            "CUST-LOYALTY-POINTS": rnd.choice([0, 0, 150, 1200, 8800, 54000, 250000]),
            "CUST-ORDER-COUNT": 0,
        })
    # deliberate edge cases
    rows[1]["CUST-MIDDLE-INIT"] = ""             # no middle initial
    rows[1]["CUST-STREET-LINE2"] = ""
    rows[4]["CUST-BALANCE"] = Decimal("-182.40")  # account in credit: negative packed decimal
    rows[7]["CUST-YTD-RETURNS"] = Decimal("-35.00")  # negative zoned decimal with overpunch sign
    rows[9].update({"CUST-CREDIT-LIMIT": Decimal("0.00"), "CUST-BALANCE": Decimal("0.00"), "CUST-STATUS": "S"})
    rows[12]["CUST-DATE-MODIFIED"] = 0           # never modified
    rows[15]["CUST-LOYALTY-POINTS"] = -500       # clawed-back points: negative binary
    return rows


# --- independent verification ----------------------------------------------------------

def parse_copybook(text: str):
    """Parse a copybook's text into [(flat_name, kind, digits, scale, signed, usage)]. Knows nothing
    about the generator's Fld/Grp classes."""
    items, groups = [], []
    for raw in text.splitlines():
        if len(raw) < 8 or raw[6] == "*":
            continue
        code = raw[7:72].strip()
        m = re.match(r"^(\d\d)\s+([A-Z0-9-]+)(?:\s+PIC\s+(\S+?))?(?:\s+(COMP-3|COMP))?\.$", code)
        if not m:
            raise ValueError(f"unparseable copybook line: {raw!r}")
        level, name, pic, usage = int(m[1]), m[2], m[3], m[4] or "DISPLAY"
        while groups and groups[-1][0] >= level:
            groups.pop()
        if pic is None:
            groups.append((level, name))
            continue
        signed = pic.startswith("S")
        kind, digits, scale, after_v = None, 0, 0, False
        for ch, n in re.findall(r"([9XV])(?:\((\d+)\))?", pic):
            count = int(n) if n else 1
            if ch == "V":
                after_v = True
            elif ch == "X":
                kind, digits = "X", digits + count
            else:
                kind, digits = "9", digits + count
                scale += count if after_v else 0
        path = [g[1] for g in groups if g[0] > 1] + [name]
        items.append(("_".join(path).replace("-", "_"), kind, digits, scale, signed, usage))
    return items


def item_length(kind, digits, usage):
    if kind == "X":
        return digits
    if usage == "COMP-3":
        return digits // 2 + 1
    if usage == "COMP":
        return 2 if digits <= 4 else 4 if digits <= 9 else 8
    return digits


def decode_item(raw: bytes, kind, digits, scale, signed, usage) -> str:
    if kind == "X":
        return raw.decode(CODEPAGE).rstrip()
    if usage == "COMP":
        n = int.from_bytes(raw, "big", signed=signed)
    elif usage == "COMP-3":
        hexs = raw.hex()
        assert hexs[-1] in "cdf", f"bad packed sign nibble in {hexs}"
        n = -int(hexs[:-1]) if hexs[-1] == "d" else int(hexs[:-1])
    else:
        assert all(b >> 4 == 0xF for b in raw[:-1]), f"bad zone nibble in {raw.hex()}"
        last = raw[-1]
        assert last >> 4 in ((0xC, 0xD, 0xF) if signed else (0xF,)), f"bad sign zone in {raw.hex()}"
        n = int("".join(str(b & 0xF) for b in raw))
        if last >> 4 == 0xD:
            n = -n
    return str(n) if not scale else str(Decimal(n).scaleb(-scale).quantize(Decimal(1).scaleb(-scale)))


def verify(name: str, root: Grp) -> int:
    cpy = parse_copybook((OUT / f"{name}.CPY").read_text(encoding="ascii"))
    reclen = sum(item_length(k, d, u) for _, k, d, _, _, u in cpy)
    blob = (OUT / f"{name}.DAT").read_bytes()
    assert reclen == record_length(root), f"{name}: copybook says {reclen}, generator says {record_length(root)}"
    assert len(blob) % reclen == 0, f"{name}: file size {len(blob)} is not a multiple of {reclen}"
    with open(OUT / "expected" / f"{name}.csv", newline="", encoding="utf-8") as fh:
        expected = list(csv.DictReader(fh))
    assert len(expected) == len(blob) // reclen, f"{name}: row count mismatch"
    for i, want in enumerate(expected):
        rec, pos = blob[i * reclen:(i + 1) * reclen], 0
        for flat, kind, digits, scale, signed, usage in cpy:
            size = item_length(kind, digits, usage)
            got = decode_item(rec[pos:pos + size], kind, digits, scale, signed, usage)
            pos += size
            if flat == "FILLER":
                assert got == "", f"{name} row {i}: FILLER not blank"
                continue
            assert got == want[flat], f"{name} row {i} {flat}: file has {got!r}, expected {want[flat]!r}"
    # a genuinely EBCDIC file: plain 'A' (0xC1) present, ASCII 'A' (0x41) not used for text
    assert b"\xc1" in blob or b"\xc9" in blob
    return len(expected)


# --- main ------------------------------------------------------------------------------

def to_csv_value(f: Fld, v):
    if f.kind == "X":
        return "" if v is None else str(v)
    if f.scale:
        return str(Decimal(v).quantize(Decimal(1).scaleb(-f.scale)))
    return str(int(v))


def emit(name: str, root: Grp, rows: list[dict], purpose: str, extra: list[str] = ()) -> None:
    (OUT / "expected").mkdir(exist_ok=True)
    write_copybook(OUT / f"{name}.CPY", f"{name}.CPY", purpose, root, extra)
    (OUT / f"{name}.DAT").write_bytes(b"".join(encode_record(root, r) for r in rows))
    cols = [(flat, f) for flat, f in leaves(root) if flat != "FILLER"]  # Cobrix drops value fillers
    with open(OUT / "expected" / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([flat for flat, _ in cols])
        for r in rows:
            w.writerow([to_csv_value(f, r.get(f.name, "")) for flat, f in cols])


def main() -> None:
    rnd = random.Random(SEED)
    products = build_products(rnd)
    customers = build_customers(rnd)
    orders = build_orders(rnd, customers, products)

    emit("CUSTOMER", CUSTOMER, customers, "Customer master with contact and account balances", [
        "CUST-TYPE  : R=Retail W=Wholesale P=Premium C=Corporate",
        "CUST-STATUS: A=Active I=Inactive S=Suspended",
        "Dates are YYYYMMDD; 00000000 means 'never modified'.",
    ])
    emit("PRODUCT", PRODUCT, products, "Product master with category hierarchy and pricing", [
        "PROD-UOM   : EACH BOX CASE PAIR SET",
        "PROD-STATUS: A=Active D=Discontinued S=Seasonal",
        "PROD-TAX-RATE is a fraction: 0.0825 = 8.25 percent.",
    ])
    emit("ORDERS", ORDERS, orders, "Order lines: one product per record, ties CUSTOMER to PRODUCT", [
        "ORD-STATUS : P=Pending S=Shipped D=Delivered C=Cancelled R=Returned",
        "ORD-PAYMENT: CC=Credit DC=Debit PP=PayPal WT=Wire CK=Check",
        "ORD-SHIP-DATE / ORD-DELIVERY-DATE are 00000000 until they happen.",
    ])

    for name, root in (("CUSTOMER", CUSTOMER), ("PRODUCT", PRODUCT), ("ORDERS", ORDERS)):
        n = verify(name, root)
        size = (OUT / f"{name}.DAT").stat().st_size
        print(f"{name:<9} {n:>3} records x {record_length(root):>3} bytes = {size:>6} bytes   verified against {name}.CPY")


if __name__ == "__main__":
    sys.exit(main())
