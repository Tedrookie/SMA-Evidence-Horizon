# EP Monitor System (J&J EP Monitor)

Automated **PubMed competitive intelligence** monitor for cardiac electrophysiology, built for **Strategic Medical Affairs (SMA) · J&J MedTech China (JJMC) / Biosense Webster**.

It retrieves recent EP papers about competitor companies and devices, stores full article records for later LLM analysis, and emails a **J&J-styled HTML digest** (with optional charts).

---

## Purpose

- Give SMA a repeatable weekly (or daily) scan of competitor EP literature.
- Centralize **what to search** (keywords, companies, products, domains, recipients, frequency) in one playbook.
- Let non-engineers change surveillance scope via an **App Manual console** without editing Python.
- Keep a durable article library (SQLite + Excel) that can be handed to an LLM later.

---

## Architecture (high level)

```
playbook.yaml  ──►  PubMed search  ──►  company/product match
                         │                      │
                         ▼                      ▼
                   SQLite library          HTML digest + charts
                   + Excel export               │
                                                ▼
                                           SMTP email (BCC)
```

Interactive control: **Streamlit console** reads/writes the same playbook and can trigger fetch / save / email.

---

## File map (for upgrades)

| Path | Role |
|------|------|
| [`ep_monitor/data/playbook.yaml`](ep_monitor/data/playbook.yaml) | **Central playbook** — domains, keywords, companies, products, schedule, recipients |
| [`ep_monitor/playbook.py`](ep_monitor/playbook.py) | Load / save / query helpers for the playbook |
| [`ep_monitor/console.py`](ep_monitor/console.py) | Streamlit App Manual UI |
| [`ep_monitor/main_basic.py`](ep_monitor/main_basic.py) | Default digest pipeline (no LLM) |
| [`ep_monitor/main.py`](ep_monitor/main.py) | Full CI pipeline with optional LLM scoring |
| [`ep_monitor/config.py`](ep_monitor/config.py) | Paths, `.env` secrets, legacy defaults |
| [`ep_monitor/pubmed_search.py`](ep_monitor/pubmed_search.py) | PubMed Boolean query + Entrez fetch |
| [`ep_monitor/company_matcher.py`](ep_monitor/company_matcher.py) | Company / product attribution |
| [`ep_monitor/database.py`](ep_monitor/database.py) | SQLite (dedupe + article library + summaries) |
| [`ep_monitor/export_excel.py`](ep_monitor/export_excel.py) | Excel export for LLM handoff |
| [`ep_monitor/email_report.py`](ep_monitor/email_report.py) | J&J-styled HTML digest + SMTP (BCC) |
| [`ep_monitor/report_charts.py`](ep_monitor/report_charts.py) | Bubble matrix + company bar charts |
| [`ep_monitor/scheduler.py`](ep_monitor/scheduler.py) | Cron / Windows Task Scheduler helpers |
| [`ep_monitor/data/articles_library.db`](ep_monitor/data/) | Full article library (gitignored) |
| [`ep_monitor/data/basic_processed.db`](ep_monitor/data/) | Email dedupe store (gitignored) |
| [`ep_monitor/exports/`](ep_monitor/exports/) | Excel snapshots (`articles_YYYY-MM-DD.xlsx`) |
| [`ep_monitor/reports/`](ep_monitor/reports/) | Saved HTML digests |
| [`.env`](.env.example) | Secrets only: NCBI, SMTP, optional LLM keys |

---

## Playbook (how to change what we collect)

Edit **`ep_monitor/data/playbook.yaml`** or use the console.

Contents include:

- **meta** — product name, owner, tagline  
- **schedule** — `weekly`/`daily`, weekday, hour, lookback days  
- **recipients** — BCC list (falls back to `EMAIL_TO` in `.env` if empty)  
- **domains[]** — each domain has technologies, diseases, EP topics, companies + products, optional own portfolio  

### Add a new domain (e.g. oncology robotics)

1. Open the console → **Playbook editor** → **Add domain template**, or copy an existing block under `domains:` in YAML.  
2. Fill keywords / companies / products.  
3. Set `enabled: true`.  
4. Save. Next PubMed run uses all enabled domains (OR’d keyword sets).

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # set NCBI_EMAIL + SMTP_*
```

### App Manual console (recommended)

```bash
python -m ep_monitor.console
```

In the browser UI you can:

1. Edit playbook (keywords, companies, recipients, schedule)  
2. Fetch PubMed & preview  
3. Save to SQLite + Excel  
4. Run full digest (optional email)  
5. Browse / export the article library  

### CLI digest (no LLM)

```bash
python -m ep_monitor.main_basic --dry-run
python -m ep_monitor.main_basic --no-email
python -m ep_monitor.main_basic --force
python -m ep_monitor.main_basic --print-schedule
```

### Full LLM pipeline (optional)

Needs `OPENAI_API_KEY` or `DEEPSEEK_API_KEY` in `.env`.

```bash
python -m ep_monitor.main --dry-run
python -m ep_monitor.main
```

---

## Article library & Excel (for LLM later)

Every successful basic run:

1. Upserts full records into `ep_monitor/data/articles_library.db`  
2. Writes `ep_monitor/exports/articles_YYYY-MM-DD.xlsx` with columns:  
   `pmid, title, abstract, journal, publication_date, authors, url, matched_companies, matched_products, domain_id, fetched_at, source`

You can also export from the console **Article library** tab.

---

## Email

- Styled like J&J All-Employee News: red accent `#c8102e`, dark header, section labels, large headlines, red text CTAs (`View on PubMed →`).  
- Recipients from playbook (BCC); SMTP credentials from `.env`.  
- Includes Visual overview charts when matplotlib is available.

---

## Scheduling

```bash
python -m ep_monitor.main_basic --print-schedule
```

Uses playbook schedule (default Monday 08:00). Prefer **Windows Task Scheduler** / cron over a long-lived Python process. Keep the PC awake (or allow wake timers) at send time.

---

## Retrieval logic (reminder)

```
(technologies ∪ EP topics)  AND  diseases  AND  (companies ∪ products)  AND  [PDAT window]
```

All three keyword groups come from **enabled domains** in the playbook.

---

## Upgrade / maintenance notes

- Prefer changing **playbook.yaml** (or console) over editing Python for keywords.  
- Keep secrets only in `.env`.  
- New intelligence sources can implement `IntelligenceSource` under `sources/` without rewriting the digest.  
- LLM summarization remains optional (`main.py`); basic digest does not require an API token.
