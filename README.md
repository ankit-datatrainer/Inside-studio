# Insight Studio

A local web app that turns the workflow in `Pandas_in_Python.ipynb` into a
repeatable five-step pipeline:

**Upload → Understand → Clean → Explore & chart → Build a dashboard and export.**

It is built on the same stack as the notebook — **pandas** for analysis and
**matplotlib** for charts — with a light Flask layer on top. Upload an Excel
workbook or CSV and use the browser; no notebook cells to edit, no Python
required for day-to-day use.

The original notebook and workbook in the parent folder are never modified.

---

## What it does

| Notebook step | In Insight Studio |
|---|---|
| `pd.read_excel(...)` | Upload `.xlsx`, `.xls`, `.xlsm`, `.csv`, `.tsv`, `.txt`; pick a sheet with a preview |
| `df.shape`, `df.columns`, `head()`, `tail()`, `info()` | **Understand** page: shape, types, missing %, duplicates, previews, `describe()` table |
| `df.isnull().sum()`, duplicate checks | Quality notes + automatic cleaning suggestions |
| Manual `fillna`, type fixes, renames | **Clean** page: toggleable steps with a before/after audit log |
| `groupby`, `value_counts`, aggregations | KPI cards, plain-English findings, ready-made tables, free-form group-by builder |
| `matplotlib` bar/line/pie charts | 13 chart types, 6 palettes, saved chart gallery |
| A combined dashboard | **Dashboard** page exporting PDF, HTML, PNG poster and Excel workbook |

Beyond the notebook:

- Cleaning is **suggested automatically** and can be previewed, toggled and re-run.
- Derived columns (e.g. `Gross_Amount`, `Discount_Amount`, `Net_Revenue`) are
  detected from quantity / price / discount columns and can be edited.
- Every chart can be downloaded as **PNG, JPG, SVG, PDF, WEBP or TIFF**.
- The dashboard can be exported as a **multi-page PDF**, a standalone HTML
  file, a poster PNG or a multi-sheet Excel analysis workbook.
- Workspaces survive server restarts and are cleaned up after 48 hours.

---

## Quick start

### Windows (one click)

Double-click **`start.bat`**. It creates a virtual environment on first run,
installs dependencies, starts the server and opens
<http://127.0.0.1:5000>.

### Windows / macOS / Linux (manual)

```bash
cd insight-studio
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. The bundled sample workbook is available with
**“Open the sample sales file”** on the landing page.

### Linux / macOS (one command)

```bash
chmod +x start.sh
./start.sh          # serves with gunicorn (waitress fallback) on 0.0.0.0:5000
```

### Docker

```bash
docker compose up --build -d
```

Open <http://localhost:8000>. Change `SECRET_KEY` in `docker-compose.yml` before
putting it on a public server. Workspace data is kept in the `studio-data`
volume.

---

## The five steps

1. **Upload** — drag in an Excel or CSV file, or use the sample workbook. If an
   Excel file has multiple sheets you get a chooser with row/column previews.
2. **Understand** — see row/column counts, detected types, missing values,
   duplicates, top values, numeric distributions, correlations and a
   `describe()`-style table. KPI cards highlight the important totals.
3. **Clean** — review the suggested plan and tick what you want:
   - standardise headers, trim spaces, unify category spellings
   - convert currency text to numbers, parse text dates
   - drop empty columns/rows and exact duplicates, optionally drop duplicate keys
   - fill missing values per column (median, mean, mode, zero, constant,
     forward/back fill, drop column or drop rows)
   - cap outliers using the IQR rule (off by default)
   - create derived columns with Excel-like formulas
   Every step writes to an audit log, and you can switch between cleaned and
   original data at any time.
4. **Insights & charts** — read auto-generated findings, then build charts from
   13 types. Suggestions are generated for the file you uploaded. Save charts,
   choose which ones belong on the dashboard, and download any chart in six
   formats. Build group-by tables and download them as CSV.
5. **Dashboard** — arrange the saved charts and tables, then export:
   - **PDF** — cover, KPI summary, findings, chart grid, tables
   - **HTML** — self-contained file you can email or host
   - **PNG** — poster-style dashboard image
   - **Excel** — KPIs, insights, column profile, selected tables and cleaned data

---

## Project layout

```
insight-studio/
├── app.py                  Flask routes and page/API wiring
├── config.py               environment-driven configuration
├── wsgi.py                 WSGI entry point (gunicorn / waitress)
├── core/
│   ├── loader.py           Excel/CSV loading, sheet previews, safe tidying
│   ├── profiler.py         schema detection, stats, quality notes
│   ├── cleaner.py          cleaning suggestions, execution and audit log
│   ├── insights.py         KPIs, plain-English findings, suggestions, tables
│   ├── charts.py           matplotlib chart factory + export formats
│   ├── reporter.py         PDF / HTML / PNG dashboard builders
│   └── workspace.py        per-session files, state and retention
├── templates/              Jinja pages
├── static/                 CSS and vanilla JS
├── sample_data/            bundled workbook + generated messy CSV
├── tests/
│   ├── smoke_test.py       44-check full journey on the notebook workbook
│   ├── messy_test.py       schema-flexibility + graceful-failure checks
│   └── make_messy_sample.py
├── deploy/                 AWS, Hostinger, VPS and nginx guides
├── Dockerfile
├── docker-compose.yml
├── start.bat / start.sh
└── requirements.txt
```

Uploaded files live under `workspaces/<session-id>/` together with
`state.json`, `cleaned.pkl`, `charts/`, `tables/` and `exports/`. The browser
only holds an opaque session id; no data is sent to a third-party service.
Use **Start over** in the sidebar to delete the current workspace immediately.
Stale workspaces are removed after `WORKSPACE_TTL_HOURS` (default 48).

---

## Chart types and exports

Chart types: bar, horizontal bar, line, area, stacked bar, pie, donut, scatter,
histogram, box plot, correlation heatmap, two-way count heatmap, and row count.

Exports: **PNG, JPG, SVG (vector), PDF (vector), WEBP, TIFF**.

Dashboard exports: **PDF, HTML, PNG, Excel**.

---

## Configuration

All settings are environment variables; nothing needs editing in code.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `change-me-in-production` | Flask session signing key — set a long random value in production |
| `HOST` | `127.0.0.1` | Bind address for `python app.py` / `start.sh` |
| `PORT` | `5000` | HTTP port |
| `FLASK_DEBUG` | `0` | Set to `1` only for local development |
| `WORKSPACE_DIR` | `./workspaces` | Where per-session data is stored |
| `UPLOAD_DIR` | `./uploads` | Upload staging directory |
| `MAX_UPLOAD_MB` | `100` | Maximum upload size |
| `WORKSPACE_TTL_HOURS` | `48` | Retention for unused workspaces |
| `CHART_DPI` | `130` | Default render DPI |
| `APP_NAME` | `Insight Studio` | Name shown in the UI and exports |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when the site is served over HTTPS only |

Example:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export PORT=8000
export WORKSPACE_TTL_HOURS=24
python app.py
```

---

## Testing

```bash
# Main journey on the notebook workbook (upload -> dashboard)
python tests/smoke_test.py

# Schema flexibility with a deliberately messy CSV
python tests/make_messy_sample.py
python tests/messy_test.py
```

Both tests run against a temporary in-process Flask client and leave the
original files untouched. The generated artifacts are written into session
workspaces under `workspaces/` and can be removed with **Start over** or by
deleting that folder.

---

## Deployment

Ready-to-follow guides:

- **AWS** — [`deploy/AWS.md`](deploy/AWS.md) (EC2 / Lightsail with Docker or
  gunicorn; notes on ephemeral platforms)
- **Hostinger** — [`deploy/HOSTINGER.md`](deploy/HOSTINGER.md) (VPS template;
  shared-hosting caveat)
- **Any VPS** — [`deploy/VPS.md`](deploy/VPS.md) (Ubuntu, systemd, nginx, TLS)
- **nginx config** — [`deploy/nginx.conf`](deploy/nginx.conf)

In every case, change `SECRET_KEY`, serve over HTTPS, and keep the
`workspaces/` directory on persistent disk. The app is a single Python service;
no database or external queue is required.

### Production notes

- On Linux use **gunicorn** behind nginx (see the guides) or the provided
  Docker image. On Windows use **waitress** (installed automatically by
  `requirements.txt`).
- `python app.py` uses Flask’s development server and is for localhost only.
- Uploads and exports are stored on local disk. If you scale to multiple
  containers, put `WORKSPACE_DIR` on shared storage (e.g. EFS) or add sticky
  sessions; each worker also keeps a small in-process profile cache.
- PDF export uses matplotlib’s PDF backend, so no LibreOffice or browser
  rendering service is needed.

---

## Troubleshooting

**Port already in use** — set another port: `set PORT=5001` (Windows) or
`export PORT=5001`, then restart.

**`No module named ...`** — activate the virtual environment and run
`pip install -r requirements.txt` again.

**Chart export fails after switching data versions** — a saved chart may use a
derived column that only exists in cleaned data. Switch back to cleaned data or
rebuild the chart; the app shows a message instead of a server error.

**Excel file is slow or huge** — the app profiles a sample of each column and
limits exports, but very large files are best cleaned before upload or run on a
server with more RAM.

**Hostinger shared hosting** — if the plan does not give you SSH, a
long-running Python process and a WSGI entry point, use a Hostinger VPS
instead. See the Hostinger guide for details.

---

## License / data

This is a local analysis tool built for your own data. Nothing is uploaded to
an external API. The source notebook and original workbook remain in the parent
folder, unmodified.
