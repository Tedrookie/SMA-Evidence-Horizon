# EP Monitor System

Automated PubMed competitive intelligence monitor for **cardiac electrophysiology**, built for **Johnson & Johnson Biosense Webster**.

Retrieves recent EP papers on competitor companies and devices, attributes them via product-name mapping, and emails an HTML digest. Optional AI summarization and importance scoring are available when an LLM API key is configured.

**What it does**
- Searches PubMed for ablation / mapping / EP papers involving competitors (Boston Scientific, Medtronic, Abbott, MicroPort, AtriCure)
- Matches companies by name and product keywords (FARAPULSE, PulseSelect, EnSite X, etc.)
- Deduplicates with SQLite so the same paper is not emailed twice
- Sends a branded HTML digest via SMTP (BCC to protect recipient privacy)
- Optional full pipeline: OpenAI / DeepSeek summaries + high-impact filtering (score ≥ 7)

---

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │            main.py (CLI)            │
                    │   orchestrates one pipeline cycle   │
                    └─────────────────┬───────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
 ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
 │ IntelligenceSource│       │   database.py   │       │ openai_summary  │
 │  (sources/base) │       │  SQLite store   │       │  LLM scoring    │
 └────────┬────────┘       └────────▲────────┘       └────────┬────────┘
          │                         │                         │
          ▼                         │                         ▼
 ┌─────────────────┐       ┌────────┴────────┐       ┌─────────────────┐
 │ pubmed_search   │──────▶│ pubmed_parser   │       │  email_report   │
 │ + query builder │       │ → Article model │       │  HTML + SMTP    │
 └─────────────────┘       └────────┬────────┘       └─────────────────┘
                                    │
                                    ▼
                         ┌─────────────────┐
                         │ company_matcher │
                         │ name + products │
                         └─────────────────┘
```

### Design principles

| Principle | How it is applied |
|-----------|-------------------|
| Modular | One responsibility per file; modules talk via `Article` / `ArticleSummary` |
| Extensible sources | `IntelligenceSource` ABC — PubMed today; ClinicalTrials, FDA, patents later |
| Product-aware CI | `COMPANY_PRODUCT_MAP` catches FARAPULSE / PulseSelect / EnSite X without company name |
| Idempotent | SQLite keyed by `(source, source_id)` prevents re-processing |
| Configurable | Env vars for secrets; `config.py` for vocabularies and thresholds |
| Schedulable | Thin CLI + OS cron / Task Scheduler (see `scheduler.py`) |

### Why product-name mapping matters

Many pivotal papers never say “Boston Scientific” — they say **FARAPULSE** or **Farawave**. The matcher scans title + abstract for product keywords and attributes the parent company before the LLM step, improving both PubMed recall (query OR products) and post-hoc classification accuracy.

---

## Workflow (one cycle)

1. **Query PubMed** — Boolean query: (technologies ∪ EP topics) ∧ diseases ∧ (companies ∪ products), last N days.
2. **Parse** — Entrez records → `Article` (title, abstract, journal, date, PMID, authors, URL).
3. **Dedupe** — Drop PMIDs already in SQLite.
4. **Company match** — Tag `matched_companies` / `matched_products` from name + device keywords.
5. **Summarize** — OpenAI returns structured CI fields + importance score 1–10.
6. **Persist** — Save summaries; mark PMIDs processed.
7. **Report** — HTML email for score ≥ threshold; save copy under `reports/`; footer with distributions.

---

## Project layout

```
ep_monitor/
├── config.py              # Vocabularies, product map, env-backed secrets
├── company_matcher.py     # Company + product attribution
├── pubmed_search.py       # Entrez search (IntelligenceSource)
├── pubmed_parser.py       # XML/dict → Article
├── openai_summary.py      # LLM summarization + scoring
├── database.py            # SQLite processed IDs + summaries
├── email_report.py        # HTML report + SMTP
├── scheduler.py           # Cron / Task Scheduler helpers
├── main.py                # Pipeline orchestration
├── models/
│   └── article.py         # Shared Article / ArticleSummary dataclasses
├── sources/
│   └── base.py            # IntelligenceSource ABC (future-proof)
├── data/                  # SQLite DB (gitignored content)
└── reports/               # Saved HTML reports
```

---

## Implementation status

| Module | Status |
|--------|--------|
| Architecture + stubs | Done |
| `config.py` (incl. product map) | Done (live config) |
| `models/` + `sources/base` | Done (live interfaces) |
| `company_matcher.py` | **Done** |
| `pubmed_search.py` | **Done** |
| `pubmed_parser.py` | **Done** |
| `database.py` | **Done** |
| `openai_summary.py` | **Done** |
| `email_report.py` | **Done** |
| `scheduler.py` | **Done** |
| `main.py` | **Done** (full CI + LLM) |
| `main_basic.py` | **Done** (PubMed + email, no LLM) |

All modules implemented.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # fill NCBI_EMAIL (+ SMTP_* for email)
```

### Basic digest (no OpenAI / no LLM)

Needs `NCBI_EMAIL`. SMTP only if you send email.

```bash
python -m ep_monitor.main_basic --dry-run
python -m ep_monitor.main_basic --no-email
python -m ep_monitor.main_basic --lookback-days 7
```

### Full CI pipeline (OpenAI / DeepSeek summary)

Needs LLM key + SMTP for email.

```bash
python -m ep_monitor.main --dry-run
python -m ep_monitor.main --no-email
python -m ep_monitor.main --print-schedule
python -m ep_monitor.main
```

### Scheduling

- **Basic:** `python -m ep_monitor.main_basic`
- **Full:** `python -m ep_monitor.main`
- **Linux/macOS cron example:** `0 7 * * 1 cd /path/to/EPMarket_News && .venv/bin/python -m ep_monitor.main_basic`
- **Windows:** Task Scheduler daily/weekly (helpers in `scheduler.py`)

---

## Future sources (no pipeline rewrite)

Add a class under `sources/` implementing `IntelligenceSource.fetch()`, register it in `main.py`. Shared `Article` model + `(source, source_id)` DB key absorb ClinicalTrials.gov, FDA, HRS/EHRA abstracts, press releases, patents, Semantic Scholar, RSS.
