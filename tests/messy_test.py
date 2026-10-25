"""Schema-flexibility test: upload a deliberately messy CSV and take it all
the way through cleaning, derived formulas, charts, tables and dashboards.

    python tests/messy_test.py

It also checks that a saved chart which references a cleaned-only column
fails gracefully (redirect + message) when the user switches back to raw data.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app import app  # noqa: E402
from config import Config  # noqa: E402
from core import loader  # noqa: E402
from core import workspace as ws_mod  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[bool, str]] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    results.append((bool(condition), label))
    print(f"{PASS if condition else FAIL} {label}" + (f"  {extra}" if extra and not condition else ""))
    return bool(condition)


def main() -> int:
    sample = ROOT / "sample_data" / "messy_retail_orders.csv"
    if not sample.exists():
        print(f"{FAIL} messy sample missing, run tests/make_messy_sample.py first: {sample}")
        return 1

    app.config.update(TESTING=True)
    client = app.test_client()

    # 1. upload -------------------------------------------------------------
    r = client.post(
        "/upload",
        data={"file": (io.BytesIO(sample.read_bytes()), "messy_retail_orders.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    check(r.status_code in (302, 303), "messy CSV upload accepted", f"status={r.status_code}")

    # 2. profile ------------------------------------------------------------
    r = client.get("/profile")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Order ID" in body, "profile detects spreadsheet headers")
    check("Currency" in body or "Number" in body or "Text" in body, "profile labels column types")
    check("Data-quality notes" in body, "profile shows data-quality notes")

    # 3. clean suggestions --------------------------------------------------
    r = client.get("/clean")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Suggested steps" in body, "clean page renders")

    with client.session_transaction() as sess:
        sid = sess["sid"]
    w = ws_mod.workspace(Config.WORKSPACE_DIR, sid)
    state = ws_mod.load_state(w)
    plan = state.get("clean_plan") or []
    types = {s["type"] for s in plan}
    check({"fix_column_names", "convert_numbers", "convert_dates", "drop_duplicates",
           "fill_missing", "derived_columns"} <= types, "clean plan detects the messy patterns")

    # 4. apply the whole plan (defaults on) --------------------------------
    form: dict[str, object] = {}
    for step in plan:
        if step.get("default_enabled"):
            form[f"enable__{step['id']}"] = "on"
    # explicitly opt into duplicate removal / normalisation / trimming
    for step in plan:
        if step["type"] in ("drop_duplicates", "trim_text", "normalize_values",
                            "convert_numbers", "convert_dates", "derived_columns"):
            form[f"enable__{step['id']}"] = "on"

    fill = next((s for s in plan if s["type"] == "fill_missing"), None)
    if fill:
        for col, cfg in fill.get("params", {}).get("strategies", {}).items():
            form[f"strategy__{col}"] = cfg.get("strategy", "leave")
            if cfg.get("value") is not None:
                form[f"value__{col}"] = cfg["value"]

    derived = next((s for s in plan if s["type"] == "derived_columns"), None)
    if derived:
        rows = derived.get("params", {}).get("rows") or []
        form["derived_name"] = [r["name"] for r in rows]
        form["derived_expr"] = [r["expression"] for r in rows]

    r = client.post("/clean/apply", data=form, follow_redirects=True)
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Cleaning applied" in body, "messy plan applies without errors")
    check("Net_Revenue" in body and "Gross_Amount" in body, "formulas created after header rename")

    # 5. inspect cleaned frame directly ------------------------------------
    cleaned = ws_mod.load_cleaned(w)
    check(cleaned is not None, "cleaned dataframe saved")
    if cleaned is not None:
        check(len(cleaned) == 80, "duplicate and empty rows removed", f"rows={len(cleaned)}")
        check(cleaned.isna().sum().sum() == 0, "all missing values handled",
              f"missing={int(cleaned.isna().sum().sum())}")
        for col in ("Order_ID", "Order_Date", "Unit_Price", "Net_Revenue", "Region"):
            check(col in cleaned.columns, f"column standardised/derived: {col}")
        if "Order_Date" in cleaned.columns:
            check(pd.api.types.is_datetime64_any_dtype(cleaned["Order_Date"]),
                  "mixed date formats parsed to datetime")
        if "Unit_Price" in cleaned.columns:
            check(pd.api.types.is_numeric_dtype(cleaned["Unit_Price"]),
                  "currency strings converted to numbers")
        if "Region" in cleaned.columns:
            check(cleaned["Region"].nunique() == 4, "inconsistent category spellings unified",
                  f"unique={cleaned['Region'].nunique()}")
        if "Net_Revenue" in cleaned.columns:
            check(float(cleaned["Net_Revenue"].sum()) > 0, "derived Net_Revenue is populated")

    # 6. insights -----------------------------------------------------------
    r = client.get("/insights")
    body = r.get_data(as_text=True)
    check(r.status_code == 200 and "Net_Revenue" in body, "insights use cleaned/derived columns")
    check("Net_Revenue over time" in body, "time-series suggestion generated for renamed date")
    check("Correlation heatmap" in body, "heatmap suggestion available")

    # 7. charts + downloads -------------------------------------------------
    specs = [
        {"chart_type": "line", "x": "Order_Date", "y": "Net_Revenue", "agg": "sum",
         "freq": "M", "title": "Revenue trend", "x_is_date": True},
        {"chart_type": "bar", "x": "Product_Category", "y": "Net_Revenue", "agg": "sum",
         "title": "Revenue by category"},
        {"chart_type": "count", "x": "Region", "title": "Orders by region"},
        {"chart_type": "heatmap", "title": "Correlation"},
    ]
    chart_ids: list[str] = []
    for spec in specs:
        r = client.post("/api/chart/render", json=spec)
        ok = r.status_code == 200 and r.get_json().get("ok")
        check(bool(ok), f"messy chart renders: {spec['chart_type']}",
              r.get_data(as_text=True)[:200] if not ok else "")
        if ok:
            chart_ids.append(r.get_json()["chart"]["id"])

    if chart_ids:
        for fmt in ("png", "jpg", "svg", "pdf", "webp", "tiff"):
            r = client.get(f"/chart/{chart_ids[0]}/download?fmt={fmt}")
            check(r.status_code == 200 and len(r.data) > 500, f"messy chart download {fmt.upper()}")

    # 8. table builder ------------------------------------------------------
    r = client.post("/api/table/build", json={
        "rows": ["Region"],
        "metrics": [{"column": None, "agg": "count"},
                    {"column": "Net_Revenue", "agg": "sum"},
                    {"column": "Units", "agg": "sum"}],
        "title": "Region summary",
        "on_dashboard": True,
    })
    ok = r.status_code == 200 and r.get_json().get("ok")
    check(bool(ok), "messy table builder", r.get_data(as_text=True)[:200] if not ok else "")
    table_id = r.get_json()["table"]["id"] if ok else None
    if table_id:
        r = client.get(f"/table/{table_id}/download?fmt=csv")
        check(r.status_code == 200 and b"Region" in r.data, "messy table CSV download")

    # 9. dashboard exports --------------------------------------------------
    for fmt, marker in (("pdf", b"%PDF"), ("html", b"<!DOCTYPE"), ("png", b"\x89PNG"),
                        ("excel", b"PK")):
        r = client.post("/api/dashboard/export", json={"fmt": fmt})
        ok = r.status_code == 200 and r.get_json().get("ok")
        if ok:
            d = client.get(r.get_json()["url"])
            ok = d.status_code == 200 and marker in d.data[:64]
            check(bool(ok), f"messy dashboard {fmt.upper()} export")
        else:
            check(False, f"messy dashboard {fmt.upper()} export", r.get_data(as_text=True)[:200])

    # 10. graceful failure when a cleaned-only column disappears -----------
    if chart_ids:
        r = client.post("/use-cleaned", data={"use": "0", "next": "/insights"},
                        follow_redirects=False)
        check(r.status_code in (302, 303), "switch to raw data")
        r = client.get(f"/chart/{chart_ids[0]}/download?fmt=png", follow_redirects=False)
        check(r.status_code in (302, 303) and r.headers.get("Location", "").endswith("/insights"),
              "missing-column chart download redirects instead of 500", f"status={r.status_code}")
        r = client.get("/insights")
        check("current data has no" in r.get_data(as_text=True),
              "friendly message shown for missing column")
        client.post("/use-cleaned", data={"use": "1", "next": "/insights"})
        r = client.get(f"/chart/{chart_ids[0]}/download?fmt=png")
        check(r.status_code == 200, "download works again after switching back to cleaned data")

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
