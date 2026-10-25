"""End-to-end smoke test for Insight Studio.

Runs the full user journey against the sample workbook:
upload -> profile -> clean -> insights -> chart render -> every download
format -> table build -> dashboard exports (pdf / html / png / excel).

    python tests/smoke_test.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[bool, str]] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    results.append((bool(condition), label))
    print(f"{PASS if condition else FAIL} {label}" + (f"  {extra}" if extra and not condition else ""))
    return bool(condition)


def main() -> int:
    sample = ROOT / "sample_data" / "pandas_practice_sales_data.xlsx"
    if not sample.exists():
        print(f"{FAIL} sample workbook missing: {sample}")
        return 1

    app.config.update(TESTING=True)
    client = app.test_client()

    # 1. landing page -------------------------------------------------------
    r = client.get("/")
    check(r.status_code == 200, "GET / landing page")

    # 2. upload the sample workbook ----------------------------------------
    data = {"file": (io.BytesIO(sample.read_bytes()), "pandas_practice_sales_data.xlsx")}
    r = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=False)
    check(r.status_code in (302, 303), "POST /upload accepted", f"status={r.status_code}")

    # multiple sheets -> sheet chooser
    if r.headers.get("Location", "").endswith("/sheet"):
        r = client.post("/sheet", data={"sheet": "Sales_Data"}, follow_redirects=False)
        check(r.status_code in (302, 303), "POST /sheet selected Sales_Data")

    # 3. profile ------------------------------------------------------------
    r = client.get("/profile")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Customer_Rating" in body, "GET /profile shows columns")
    check("describe()" in body or "describe" in body, "profile includes describe() block")
    check("Data-quality notes" in body, "profile includes quality notes")

    # 4. clean --------------------------------------------------------------
    r = client.get("/clean")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Suggested steps" in body, "GET /clean shows suggested steps")

    form = {
        "enable__fix_column_names": "on",
        "enable__trim_text": "on",
        "enable__normalize_values": "on",
        "enable__convert_numbers": "on",
        "enable__convert_dates": "on",
        "enable__drop_duplicates": "on",
        "enable__fill_missing": "on",
        "enable__derived_columns": "on",
        "strategy__Customer_Rating": "median",
        "derived_name": ["Gross_Amount", "Discount_Amount", "Net_Revenue"],
        "derived_expr": ["Quantity * Unit_Price",
                         "(Gross_Amount * Discount_Pct / 100)",
                         "(Gross_Amount - Discount_Amount)"],
    }
    r = client.post("/clean/apply", data=form, follow_redirects=True)
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Cleaning applied" in body, "POST /clean/apply runs pipeline")
    check("Gross_Amount" in body and "Net_Revenue" in body, "derived columns created")

    # 5. insights -----------------------------------------------------------
    r = client.get("/insights")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Key findings" in body, "GET /insights shows findings")
    check("Suggested for this file" in body, "insights page shows chart suggestions")
    check("Group-by table builder" in body, "insights page shows table builder")

    # 6. render chart + downloads ------------------------------------------
    specs = [
        {"chart_type": "line", "x": "Order_Date", "y": "Net_Revenue", "agg": "sum",
         "freq": "M", "title": "Net revenue by month", "x_is_date": True},
        {"chart_type": "bar", "x": "Category", "y": "Net_Revenue", "agg": "sum",
         "title": "Net revenue by category", "top_n": 10},
        {"chart_type": "donut", "x": "Payment_Mode", "agg": "count", "title": "Payment mix"},
        {"chart_type": "hist", "x": "Unit_Price", "bins": 15, "title": "Price distribution"},
        {"chart_type": "scatter", "x": "Unit_Price", "y": "Net_Revenue", "title": "Price vs revenue"},
        {"chart_type": "box", "x": "Category", "y": "Net_Revenue", "title": "Revenue spread"},
        {"chart_type": "stacked_bar", "x": "Order_Date", "y": "Net_Revenue", "group": "Category",
         "agg": "sum", "freq": "M", "title": "Category mix by month", "x_is_date": True},
        {"chart_type": "heatmap", "title": "Correlation"},
        {"chart_type": "heatmap_counts", "x": "City", "y": "Payment_Mode", "title": "City vs payment"},
        {"chart_type": "count", "x": "City", "title": "Orders by city"},
        {"chart_type": "hbar", "x": "City", "y": "Quantity", "agg": "sum", "title": "Units by city"},
        {"chart_type": "area", "x": "Order_Date", "y": "Quantity", "agg": "sum", "freq": "W",
         "title": "Weekly units", "x_is_date": True},
        {"chart_type": "pie", "x": "Category", "agg": "count", "title": "Category share"},
    ]

    chart_ids = []
    for spec in specs:
        r = client.post("/api/chart/render", json=spec)
        ok = r.status_code == 200 and r.get_json().get("ok")
        check(bool(ok), f"chart render: {spec['chart_type']}",
              r.get_data(as_text=True)[:200] if not ok else "")
        if ok:
            chart_ids.append(r.get_json()["chart"]["id"])

    # downloads in every format for the first chart
    if chart_ids:
        cid = chart_ids[0]
        for fmt in ("png", "jpg", "svg", "pdf", "webp", "tiff"):
            r = client.get(f"/chart/{cid}/download?fmt={fmt}")
            ok = r.status_code == 200 and len(r.data) > 500
            check(ok, f"download chart as {fmt.upper()}",
                  f"status={r.status_code} bytes={len(r.data)}")

    # 7. table builder ------------------------------------------------------
    payload = {
        "rows": ["Category"],
        "metrics": [{"column": None, "agg": "count"},
                    {"column": "Quantity", "agg": "sum"},
                    {"column": "Net_Revenue", "agg": "sum"},
                    {"column": "Net_Revenue", "agg": "mean"},
                    {"column": "Customer_Rating", "agg": "mean"}],
        "title": "Category summary",
        "on_dashboard": True,
    }
    r = client.post("/api/table/build", json=payload)
    ok = r.status_code == 200 and r.get_json().get("ok")
    check(bool(ok), "table builder: Category summary", r.get_data(as_text=True)[:200] if not ok else "")
    table_id = r.get_json()["table"]["id"] if ok else None
    if table_id:
        r = client.get(f"/table/{table_id}/download?fmt=csv")
        check(r.status_code == 200 and b"Category" in r.data, "table CSV download")

    # city + payment cross table
    r = client.post("/api/table/build", json={
        "rows": ["City", "Payment_Mode"],
        "metrics": [{"column": None, "agg": "count"}, {"column": "Net_Revenue", "agg": "sum"}],
        "title": "City x Payment", "on_dashboard": False,
    })
    check(r.status_code == 200 and r.get_json().get("ok"), "table builder: two-column group-by")

    # 8. toggle chart off dashboard then on again ---------------------------
    if chart_ids:
        r = client.post("/api/chart/dashboard", json={"id": chart_ids[0], "on": False})
        check(r.status_code == 200 and r.get_json().get("ok"), "chart dashboard toggle off")
        r = client.post("/api/chart/dashboard", json={"id": chart_ids[0], "on": True})
        check(r.status_code == 200 and r.get_json().get("ok"), "chart dashboard toggle on")

    # 9. dashboard page -----------------------------------------------------
    r = client.get("/dashboard")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Dashboard &amp; export" in body, "GET /dashboard renders")

    # 10. exports -----------------------------------------------------------
    for fmt, marker in (("pdf", b"%PDF"), ("html", b"<!DOCTYPE"), ("png", b"\x89PNG"),
                        ("excel", b"PK")):
        r = client.post("/api/dashboard/export", json={"fmt": fmt})
        ok = r.status_code == 200 and r.get_json().get("ok")
        if ok:
            url = r.get_json()["url"]
            d = client.get(url)
            ok = d.status_code == 200 and d.data[:8].startswith(marker[:4]) or marker in d.data[:64]
            check(bool(ok), f"dashboard export {fmt.upper()} downloads",
                  f"status={d.status_code} size={len(d.data)}")
        else:
            check(False, f"dashboard export {fmt.upper()}", r.get_data(as_text=True)[:200])

    # 11. cleaned data downloads -------------------------------------------
    r = client.get("/download/cleaned?fmt=csv")
    check(r.status_code == 200 and b"Net_Revenue" in r.data, "cleaned CSV download")
    r = client.get("/download/cleaned?fmt=xlsx")
    check(r.status_code == 200 and r.data[:2] == b"PK", "cleaned Excel download")

    # 12. health ------------------------------------------------------------
    r = client.get("/healthz")
    check(r.status_code == 200 and r.get_json().get("ok"), "health check")

    # summary ---------------------------------------------------------------
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 64)
    print(f"RESULT: {passed}/{total} checks passed")
    if passed != total:
        print("Failed checks:")
        for ok, label in results:
            if not ok:
                print(f"  - {label}")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
