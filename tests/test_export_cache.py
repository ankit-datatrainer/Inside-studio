import io
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from app import app, cached_audit, _AUDIT_CACHE, _EXPORT_BYTES_CACHE


class TestExportCache(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        _AUDIT_CACHE.clear()
        _EXPORT_BYTES_CACHE.clear()

    def test_cached_audit_function(self):
        df = pd.DataFrame({
            "product": ["Widget A", "Widget B", "Widget A", None],
            "sales": [100.0, 250.0, 100.0, 50.0],
            "units": [10, 25, 10, 5],
        })
        with app.test_request_context():
            key = "test_key_1"
            audit1 = cached_audit(df, key, "Test Source")
            self.assertIn("overview", audit1)
            self.assertIn("missing_analysis", audit1)

            # Second call should return the exact same cached object reference
            audit2 = cached_audit(df, key, "Test Source")
            self.assertIs(audit1, audit2)

    def test_quality_download_endpoints_speed_and_status(self):
        sample = ROOT / "sample_data" / "pandas_practice_sales_data.xlsx"
        if not sample.exists():
            self.skipTest("Sample dataset not found")

        # Upload dataset
        with self.client:
            data = {"file": (io.BytesIO(sample.read_bytes()), "pandas_practice_sales_data.xlsx")}
            r = self.client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=False)
            if r.headers.get("Location", "").endswith("/sheet"):
                self.client.post("/sheet", data={"sheet": "Sales_Data"}, follow_redirects=False)

            # Hit quality page to pre-audit
            t0 = time.time()
            rq = self.client.get("/quality")
            self.assertEqual(rq.status_code, 200)

            # Test PDF export with timing benchmark
            t1 = time.time()
            r_pdf = self.client.get("/quality/download/pdf")
            duration_pdf = time.time() - t1
            self.assertEqual(r_pdf.status_code, 200)
            self.assertEqual(r_pdf.mimetype, "application/pdf")
            self.assertTrue(len(r_pdf.data) > 1000)
            self.assertLess(duration_pdf, 2.0, f"PDF export took too long: {duration_pdf:.3f}s")

            # Subsequent PDF call must be instant from cache
            t2 = time.time()
            r_pdf_cached = self.client.get("/quality/download/pdf")
            duration_cached = time.time() - t2
            self.assertEqual(r_pdf_cached.status_code, 200)
            self.assertLess(duration_cached, 0.2, f"Cached PDF export should be under 200ms: {duration_cached:.3f}s")

            # Test CSV export
            r_csv = self.client.get("/quality/download/csv")
            self.assertEqual(r_csv.status_code, 200)
            self.assertEqual(r_csv.mimetype, "text/csv")
            self.assertIn("Quality Dimension", r_csv.data.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
