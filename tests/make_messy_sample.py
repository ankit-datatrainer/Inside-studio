"""Create a deliberately messy CSV used to prove the app is schema-agnostic.

The file has:
  * headers with spaces / a trailing space / a percent sign
  * text numbers with currency symbols and thousands separators
  * mixed date formats (ISO and MM/DD/YYYY)
  * inconsistent category spellings and padded text
  * missing values, duplicate rows, duplicate order ids, empty rows/columns
  * a column that is almost entirely empty

Run:  python tests/make_messy_sample.py
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data" / "messy_retail_orders.csv"

REGIONS = ["north", "North", " NORTH ", "south", "South ", "east", "West"]
CATEGORIES = ["Electronics", "electronics", "Home & Kitchen", "home & kitchen ",
              "Sports", "SPORTS", "Apparel", "Office"]
CUSTOMERS = ["Aarav Shah", "Meera Iyer", "Rohan Gupta", "Sara Khan", "Vikram Rao",
             "Ananya Bose", "Kabir Mehta", "Isha Nair", "Dev Patel", "Tara Singh"]
NOTES = ["gift wrap", "priority delivery", "call before shipping", "", "", "", "", ""]

HEADERS = ["Order ID", "Order Date", "Customer Name", "Region ", "Product Category",
           "Units", "Unit Price", "Discount %", "Rating", "Notes", "Empty"]

start = date(2025, 1, 1)
rows: list[list[str]] = []

for i in range(1, 81):
    day = start + timedelta(days=int((i - 1) * 185 / 79))
    date_text = day.isoformat() if i % 2 else day.strftime("%m/%d/%Y")
    price = random.choice([99, 149.5, 299, 499, 799, 1299, 2499])
    price_text = random.choice([
        f"${price:,.2f}",
        f"₹{price:,.0f}",
        f"{price:,.2f}",
        f"{price:.0f}",
    ])
    units = random.randint(1, 10)
    discount = random.choice(["0", "5", "10", "15", "20", "25"])
    rating = random.choice(["1", "2", "3", "4", "5", "4", "5", "N/A", "unknown"])
    region = random.choice(REGIONS)
    category = random.choice(CATEGORIES)
    customer = random.choice(CUSTOMERS) if i % 19 else f"  {random.choice(CUSTOMERS)}  "

    if i % 23 == 0:
        units_text = ""
    else:
        units_text = str(units)
    if i % 29 == 0:
        price_text = ""
    if i % 31 == 0:
        region = ""
    if i % 17 == 0:
        rating = ""

    # duplicate order ids: a few orders appear twice with a different product
    order_id = f"ORD-{i:04d}" if i % 21 else f"ORD-{max(1, i - 1):04d}"

    rows.append([
        order_id, date_text, customer, region, category, units_text, price_text,
        discount, rating, random.choice(NOTES), "",
    ])

# exact duplicate rows
rows.extend([list(rows[3]), list(rows[27]), list(rows[51])])
# fully empty row
rows.append([""] * len(HEADERS))

with OUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh)
    writer.writerow(HEADERS)
    writer.writerows(rows)

print(f"wrote {OUT} ({len(rows)} rows, {len(HEADERS)} columns)")
