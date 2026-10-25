# Export Progress Bar & One-Click AI Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide an animated progress bar and high-speed export engine for reports (PDF, CSV, Excel), and implement a One-Click AI Executive Dashboard generator that automatically discovers KPIs, analyzes key business stories with AI, and renders 4–6 complementary charts in a single click.

**Architecture:** 
1. **Export Acceleration & Caching**: Cache quality audit summaries and pre-compiled export documents in memory and workspace cache to eliminate duplicate recalculations and reduce export latency from seconds to milliseconds.
2. **Staged Progress Modal UI**: A universal client-side modal with smooth animated progress bar, percentage counter, and status messages that triggers downloads via asynchronous Blob streaming.
3. **One-Click AI Dashboard Engine**: A new backend endpoint (`/api/dashboard/auto-build`) combining smart statistical KPI discovery with OpenRouter AI analysis (strictly whitelabeled without exposing platform or model names) to auto-select, auto-render, and mount 4–6 publication-grade charts and executive narrative onto the dashboard.

**Tech Stack:** Python 3.10+, Flask, Pandas, NumPy, Matplotlib, ReportLab / PdfPages, JavaScript (Vanilla ES6), CSS3 (iCapital theme).

---

### Task 1: Quality Audit & Export Caching Optimization (Backend)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/app.py:456-495`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/core/quality.py:588-680`

**Step 1: Write the failing test for audit and export caching**
Create `tests/test_export_cache.py`:
- Test that calling `get_cached_audit(df, sid)` executes the computation once and retrieves from cache on subsequent calls.
- Test that `/quality/download/pdf` returns `200` and generates valid PDF bytes in under 1.5 seconds.

**Step 2: Run test to verify it fails**
Run: `python -m unittest tests/test_export_cache.py`

**Step 3: Implement Caching & Rendering Speedups**
1. In `app.py`:
   - Add `_AUDIT_CACHE: dict[str, tuple[str, dict[str, Any]]]` to hold `(dataset_cache_key, audit_dict)`.
   - In `quality_page()` and `quality_download()`, retrieve from `_AUDIT_CACHE` if key matches, skipping redundant scans.
   - Cache generated PDF/CSV bytes in `_EXPORT_BYTES_CACHE` for the current dataset state.
2. In `core/quality.py`:
   - Optimize `generate_pdf_report`:
     - Reduce duplicate drawing passes in `FancyBboxPatch`.
     - Set `rasterized=True` on table background elements for rapid vector compilation.
     - Limit matrix table row drawing to top 15 critical columns with high-impact layout.

**Step 4: Run test to verify it passes**
Run: `python -m unittest tests/test_export_cache.py`

---

### Task 2: Universal Staged Progress Bar Modal Component (Frontend CSS & JS)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/static/css/style.css`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/static/js/app.js`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/templates/base.html`

**Step 1: Create Progress Modal Markup in `templates/base.html`**
- Add `#export-progress-modal` into `templates/base.html` with:
  - Header: Document / Export icon + Title ("Preparing Your Export")
  - Progress bar container: glowing progress track, dynamic percentage text (`0%` -> `100%`)
  - Status indicator: Step text ("Auditing dataset...", "Compiling publication layout...", "Finalizing download...")
  - Cancel / close control button.

**Step 2: Style Progress Modal in `static/css/style.css`**
- Style the modal following the iCapital theme:
  - Deep midnight background `#0B1528`, frosted border `rgba(0,102,255,0.2)`.
  - Electric cyan / royal blue gradient progress fill (`linear-gradient(90deg, #0066FF, #23C0FF)`).
  - Subtle pulse animation on the progress bar.
  - Clean monospace percentage counter and status message typography.

**Step 3: Implement `downloadWithProgress` in `static/js/app.js`**
- Create global function:
  ```javascript
  window.downloadWithProgress = async function(url, filename, options = {}) { ... }
  ```
- Steps:
  1. Opens `#export-progress-modal` with custom title and step labels.
  2. Runs animated progress increments (e.g. 15% -> 45% -> 75% -> 90%) during the fetch request.
  3. Uses `fetch(url)` to retrieve binary data as `Blob`.
  4. Once resolved, jumps to 100% ("Download Ready!").
  5. Creates temporary `<a>` element with `URL.createObjectURL(blob)` and triggers browser save dialog.
  6. Automatically dismisses modal after 600ms and triggers success toast.

---

### Task 3: Progress Bar Integration on Quality Page & Dashboard (Frontend Templates)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/templates/quality.html:15-30`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/templates/dashboard.html:20-45, 130-147`

**Step 1: Update `templates/quality.html` Export Buttons**
- Change PDF, CSV, and JSON download buttons from plain `<a>` links to interactive buttons with `data-url` and `data-fmt`:
  - `Download Report (PDF)`: calls `downloadWithProgress('/quality/download/pdf', 'data_quality_report.pdf', {title: 'Data Quality Executive Report', steps: ['Auditing dataset metrics...', 'Generating executive scorecard...', 'Compiling PDF document...']})`.
  - `Export CSV`: calls `downloadWithProgress('/quality/download/csv', 'data_quality_issues.csv', {title: 'Quality Issues Matrix'})`.

**Step 2: Update `templates/dashboard.html` Export Buttons**
- Wire up the 4 dashboard export buttons (`PDF dashboard`, `HTML dashboard`, `Poster PNG`, `Excel workbook`) to use `downloadWithProgress` with format-specific titles and step text.

---

### Task 4: Automatic Executive KPI Discovery & AI Analysis Engine (Backend)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/core/insights.py:65-125`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/core/llm.py:365-420`

**Step 1: Write test for Executive KPI Discovery & Narrative**
Create `tests/test_executive_dashboard.py`:
- Test that `discover_executive_kpis(df, profile)` extracts at least 4 prominent business KPIs (Primary Volume, Average Transaction, Top Segment Contributor, Unique Entities, Quality Health).
- Test that `generate_dashboard_narrative_ai()` returns structured takeaways without mentioning platform/model names.

**Step 2: Implement `discover_executive_kpis` in `core/insights.py`**
- Calculate:
  1. **Primary Volume / Revenue**: Sum of top numeric/currency column with formatted abbreviator (e.g. `$4.2M` or `18,450 Units`).
  2. **Average Order / Ticket Size**: Mean of primary transaction column.
  3. **Top Growth Driver / Leader**: Category that represents the highest contribution (e.g. `Electronics ($42,100 · 34%)`).
  4. **Active Scope**: Count of unique entities/customers/SKUs.
  5. **Data Health & Reliability**: Overall quality score from audit engine (e.g. `99.3% Grade A`).

**Step 3: Implement `generate_dashboard_narrative_ai` in `core/llm.py`**
- Sends data context to backend model asking for:
  - 3 Strategic Executive Takeaways (Executive Summary, Key Growth Driver, Operational Watchpoint).
  - 4 to 6 Complementary Chart Blueprints (Trend Line, Segment Breakdown, Correlation/Scatter, Distribution).
- Includes strict confidential instruction: never mention model name or OpenRouter.
- Built-in deterministic fallback if AI is slow or unreachable.

---

### Task 5: One-Click AI Dashboard Endpoint & Rendering Pipeline (Backend)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/app.py:955-1040`

**Step 1: Implement `@app.post("/api/dashboard/auto-build")`**
- Pipeline:
  1. Retrieve active dataframe and profile.
  2. Compute executive KPIs.
  3. Query `generate_dashboard_narrative_ai` (or smart heuristic fallback).
  4. For each recommended chart (4 to 6 charts):
     - Call `charts_mod.render_chart(df, spec, out_path)`.
     - Save metadata into workspace chart registry with `on_dashboard = True`.
  5. Build and save 1–2 automated summary tables with `on_dashboard = True`.
  6. Store executive AI takeaways into workspace state.
  7. Return JSON:
     ```json
     {
       "ok": true,
       "charts_created": 5,
       "kpis": [...],
       "narrative": [...]
     }
     ```

---

### Task 6: Dashboard UI Overhaul with 1-Click Generation & Executive Narrative (Frontend)

**Files:**
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/templates/dashboard.html`
- Modify: `c:/Users/ankit/OneDrive/Desktop/Data analysis project/insight-studio/static/css/style.css`

**Step 1: Add "1-Click AI Dashboard" Hero Section**
- If dashboard has 0 charts, display prominent hero card:
  - Title: *"Generate Complete Executive Dashboard in 1-Click"*
  - Subtitle: *"AI analyzes your data patterns, finds key business KPIs, and renders 4–6 publication-grade charts instantly."*
  - Action Button: `<button id="auto-build-btn" class="btn primary lg">⚡ Build Complete Dashboard in 1-Click</button>`
- If charts already exist, keep a secondary button in the header: *"Auto-Refresh AI Dashboard"*.

**Step 2: Add "Executive Intelligence & Strategic Insights" Section**
- Render AI executive takeaways card:
  - 3 strategic callout blocks: Key Takeaway, Growth Driver, and Operational Watchpoint.
  - Badges with key numbers and trends.

**Step 3: Add Staged Progress Bar on 1-Click Dashboard Build**
- When clicked, open the progress modal:
  - 20%: "Analyzing dataset schema & business metrics..."
  - 45%: "Extracting executive KPIs & strategic takeaways..."
  - 70%: "Rendering 4-6 publication-grade charts..."
  - 95%: "Assembling dashboard layout..."
  - 100%: "Dashboard built! Reloading view..."
- Automatically refreshes the dashboard view with all charts, tables, KPIs, and narrative ready.

---

### Task 7: Comprehensive Verification & Benchmarking

**Step 1: Run Python Unit & Integration Tests**
- Run `pytest` or `python -m unittest discover tests`.
- Verify export generation time is under 1.5s.

**Step 2: Visual Browser Verification**
- Open `http://127.0.0.1:5000/quality`: test "Download Report (PDF)" and "Export CSV" with progress bar modal.
- Open `http://127.0.0.1:5000/dashboard`: click "Build Complete Dashboard in 1-Click", verify progress modal, verify 4-6 charts and KPIs appear immediately.
- Test export to PDF, HTML, PNG, and Excel from the newly built dashboard.
- Verify 100% absence of any OpenRouter, NVIDIA, or Nemotron mentions.
