# Evidence Horizon

**Strategic Medical Affairs literature monitor** for J&J MedTech China (JJMC). Starts with **EP, NV, and Surgery**; Medical Affairs can add domains later via Excel or the console.

It retrieves recent PubMed papers on J&J and competitor companies, stores full article records for later analysis, and emails a **J&J News–styled HTML digest**.

**Tagline:** *From Evidence to Strategic Insight.*

---

## Purpose

- Give SMA a repeatable scan of literature across MedTech domains (not EP-only).
- Centralize **what to search** (domains, keywords, competitor companies, J&J portfolio, recipients, schedule) in one playbook.
- Let MA edit the playbook in **Excel** and upload it in the App Manual console (YAML/form editor still available).
- Keep a durable article library (SQLite + Excel) for later LLM / deeper analysis.

---

## Architecture (high level)

```
Excel / playbook.yaml  ──►  PubMed search  ──►  company/product match
                                  │                      │
                                  ▼                      ▼
                            SQLite library          HTML digest
                            + Excel export               │
                                                         ▼
                                                    SMTP email (BCC)
```

Interactive control: **Streamlit console** (`python -m ep_monitor.console`).

---

## File map

| Path | Role |
|------|------|
| [`ep_monitor/data/playbook.yaml`](ep_monitor/data/playbook.yaml) | Active playbook (domains, keywords, companies, schedule, recipients) |
| [`ep_monitor/data/evidence_horizon_playbook_template.xlsx`](ep_monitor/data/evidence_horizon_playbook_template.xlsx) | Starter Excel MA can edit and re-upload |
| [`ep_monitor/excel_playbook.py`](ep_monitor/excel_playbook.py) | Excel template write / import |
| [`ep_monitor/playbook.py`](ep_monitor/playbook.py) | Load / save / query helpers |
| [`ep_monitor/console.py`](ep_monitor/console.py) | Streamlit App Manual UI |
| [`ep_monitor/main_basic.py`](ep_monitor/main_basic.py) | Default digest pipeline (no LLM) |
| [`ep_monitor/main.py`](ep_monitor/main.py) | Optional LLM scoring pipeline |
| [`ep_monitor/pubmed_search.py`](ep_monitor/pubmed_search.py) | PubMed Boolean query + Entrez fetch |
| [`ep_monitor/company_matcher.py`](ep_monitor/company_matcher.py) | Company / product attribution (incl. J&J) |
| [`ep_monitor/database.py`](ep_monitor/database.py) | SQLite (dedupe + article library) |
| [`ep_monitor/export_excel.py`](ep_monitor/export_excel.py) | Article library Excel export |
| [`ep_monitor/email_report.py`](ep_monitor/email_report.py) | J&J News HTML digest + SMTP (BCC) |
| [`ep_monitor/scheduler.py`](ep_monitor/scheduler.py) | Cron / Windows Task Scheduler helpers |
| [`.env`](.env.example) | Secrets only: NCBI, SMTP, optional LLM keys |

---

## Playbook for MA (Excel first)

1. Open console → **Playbook** tab → **Download starter Excel playbook**.  
2. Edit sheets: **Settings**, **Domains**, **Companies**, **Instructions**.  
   - Focus on **competitor company names** per domain; products are optional.  
   - J&J / Biosense Webster / Ethicon / Cerenovus portfolio can be listed under own portfolio.  
3. **Upload** the file and click **Apply uploaded Excel to playbook**.  
4. Form/YAML editor remains as a backup for fine edits.

Enabled domains (default: EP, NV, Surgery) are OR’d into one PubMed query.

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

### CLI digest (no LLM)

```bash
python -m ep_monitor.main_basic --dry-run
python -m ep_monitor.main_basic --no-email
python -m ep_monitor.main_basic --force
python -m ep_monitor.main_basic --print-schedule
```

---

## Digest format

- Masthead: slogan → **SMA Evidence Horizon: {digest_title}**  
- **Summary:** Domain (full name); Articles Published (date range); Papers in this digest; J&J / other companies products used  
- **Articles:** `Paper i/N` with first/last author + institute, products used from companies, full abstract

---

## Scheduling

```bash
python -m ep_monitor.main_basic --print-schedule
```

Uses playbook schedule (default Monday 08:00). Prefer **Windows Task Scheduler** / cron. Keep the PC awake (or allow wake timers) at send time.

---

## Retrieval logic

```
(technologies ∪ domain topics)  AND  diseases  AND  (companies ∪ products ∪ J&J portfolio)  AND  [PDAT window]
```

All keyword groups come from **enabled domains** in the playbook.

---

## Upgrade notes

- Prefer Excel upload or `playbook.yaml` / console over editing Python for keywords.  
- Keep secrets only in `.env`.  
- LLM summarization remains optional (`main.py`).
