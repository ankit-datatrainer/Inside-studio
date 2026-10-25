import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from core.insights import discover_executive_kpis
from core.llm import generate_dashboard_narrative_ai
from core import profiler, quality as quality_mod
from app import app, _AUDIT_CACHE, _EXPORT_BYTES_CACHE


class TestExecutiveDashboard(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        _AUDIT_CACHE.clear()
        _EXPORT_BYTES_CACHE.clear()
        self.df = pd.DataFrame({
            "Category": ["Electronics", "Furniture", "Electronics", "Office Supplies", "Furniture", "Electronics"],
            "Region": ["North", "North", "South", "South", "East", "West"],
            "Sales": [1200.0, 450.0, 890.0, 150.0, 620.0, 1450.0],
            "Quantity": [3, 2, 2, 5, 4, 3],
            "Discount": [0.05, 0.10, 0.0, 0.15, 0.05, 0.0],
            "Rating": [4.8, 3.9, 4.5, 4.1, 4.2, 4.9],
        })
        self.profile = profiler.profile_frame(self.df)

    def test_discover_executive_kpis(self):
        audit = quality_mod.audit_dataset_quality(self.df, "Test Sales Data")
        kpis = discover_executive_kpis(self.df, self.profile, audit)
        self.assertIsInstance(kpis, list)
        self.assertGreaterEqual(len(kpis), 4)

        labels = [k["label"].lower() for k in kpis]
        # Should include volume/revenue, average ticket/sales, top contributor/leader, and health
        has_volume = any("sales" in l or "revenue" in l or "volume" in l or "total" in l for l in labels)
        has_avg = any("avg" in l or "average" in l for l in labels)
        has_top = any("top" in l or "leader" in l or "contributor" in l or "share" in l for l in labels)
        has_health = any("health" in l or "quality" in l for l in labels)

        self.assertTrue(has_volume, f"Expected volume/revenue KPI, got labels: {labels}")
        self.assertTrue(has_avg, f"Expected average KPI, got labels: {labels}")
        self.assertTrue(has_top, f"Expected top segment contributor KPI, got labels: {labels}")
        self.assertTrue(has_health, f"Expected health/quality KPI, got labels: {labels}")

    def test_generate_dashboard_narrative_whitelabeling(self):
        kpis = discover_executive_kpis(self.df, self.profile)
        # Test fallback / offline path first (empty api key)
        narrative = generate_dashboard_narrative_ai("", self.df, self.profile, kpis)
        self.assertIn("summary", narrative)
        self.assertIn("growth_driver", narrative)
        self.assertIn("watchpoint", narrative)
        self.assertIn("recommended_charts", narrative)
        self.assertGreaterEqual(len(narrative["recommended_charts"]), 4)

        # Check strict whitelabeling: zero mentions of OpenRouter, Nemotron, NVIDIA
        text_dump = str(narrative).lower()
        self.assertNotIn("openrouter", text_dump)
        self.assertNotIn("nemotron", text_dump)
        self.assertNotIn("nvidia", text_dump)

    def test_api_dashboard_auto_build(self):
        sample = ROOT / "sample_data" / "pandas_practice_sales_data.xlsx"
        if not sample.exists():
            self.skipTest("Sample dataset not found")

        with self.client:
            data = {"file": (io.BytesIO(sample.read_bytes()), "pandas_practice_sales_data.xlsx")}
            r = self.client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=False)
            if r.headers.get("Location", "").endswith("/sheet"):
                self.client.post("/sheet", data={"sheet": "Sales_Data"}, follow_redirects=False)

            # Trigger 1-Click Dashboard Build
            res = self.client.post("/api/dashboard/auto-build")
            self.assertEqual(res.status_code, 200)
            json_data = res.get_json()
            self.assertTrue(json_data.get("ok"))
            self.assertGreaterEqual(json_data.get("charts_created", 0), 3)
            self.assertIn("kpis", json_data)
            self.assertIn("narrative", json_data)

            # Check that /dashboard page now renders charts and narrative
            r_dash = self.client.get("/dashboard")
            self.assertEqual(r_dash.status_code, 200)
            dash_html = r_dash.get_data(as_text=True)
            self.assertIn("Executive Intelligence", dash_html)
            self.assertIn("chart-img", dash_html)


if __name__ == "__main__":
    unittest.main()
