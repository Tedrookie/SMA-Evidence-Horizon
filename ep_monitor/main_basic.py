"""Basic PubMed digest — no LLM / OpenAI required.

Fetches recent EP competitor papers, attaches company/product matches,
and emails a basic HTML listing (title, journal, date, PMID, authors,
abstract preview, PubMed link).

The full CI pipeline with AI summaries remains available via::

    python -m ep_monitor.main

Usage
-----
    python -m ep_monitor.main_basic
    python -m ep_monitor.main_basic --lookback-days 7 --no-email
    python -m ep_monitor.main_basic --dry-run
    python -m ep_monitor.main_basic --force   # re-email even if already sent
    python -m ep_monitor.main_basic --print-schedule
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from ep_monitor import config
from ep_monitor.company_matcher import match_articles
from ep_monitor.database import ArticleDatabase
from ep_monitor.email_report import (
    basic_subject,
    build_basic_html_report,
    save_report,
    send_email,
)
from ep_monitor.pubmed_search import search_pubmed
from ep_monitor.scheduler import print_schedule_instructions

logger = logging.getLogger(__name__)

# Separate source key so basic runs do not block the full LLM pipeline later.
_BASIC_SOURCE = "pubmed_basic"
_BASIC_DB = config.DATA_DIR / "basic_processed.db"


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def run_basic_pipeline(
    *,
    lookback_days: int,
    send_email_flag: bool,
    dry_run: bool,
    force: bool = False,
) -> int:
    """Run PubMed → match → basic HTML email (no LLM)."""
    report_day = date.today()
    logger.info("=" * 60)
    logger.info("J&J EP Monitor — EP PubMed Digest — %s", report_day.isoformat())
    logger.info(
        "lookback_days=%s dry_run=%s send_email=%s force=%s (no LLM)",
        lookback_days,
        dry_run,
        send_email_flag,
        force,
    )
    logger.info("=" * 60)

    articles = search_pubmed(lookback_days=lookback_days)
    total_found = len(articles)
    logger.info("Fetched %d article(s) from PubMed", total_found)

    # Tag with basic source so dedupe is isolated from the full pipeline DB.
    for article in articles:
        article.source = _BASIC_SOURCE

    with ArticleDatabase(_BASIC_DB) as db:
        if force:
            new_articles = articles
            logger.info(
                "Force mode: skipping dedupe; reporting all %d fetched article(s)",
                len(new_articles),
            )
        else:
            new_articles = db.filter_new(articles)
            logger.info("%d new article(s) after dedupe", len(new_articles))

        if not new_articles:
            logger.info("Nothing new to report. Exiting successfully.")
            return 0

        match_articles(new_articles, competitors_only=True)
        attributed = sum(1 for a in new_articles if a.matched_companies)
        logger.info(
            "Attributed %d / %d article(s) to competitors",
            attributed,
            len(new_articles),
        )

        if dry_run:
            for article in new_articles:
                logger.info(
                    "  would email PMID=%s companies=%s | %s",
                    article.source_id,
                    article.matched_companies or ["Unknown"],
                    article.title[:100],
                )
            logger.info("Dry-run complete; nothing emailed or marked processed.")
            return 0

        html = build_basic_html_report(
            new_articles,
            report_date=report_day,
            total_found=total_found,
            lookback_days=lookback_days,
        )
        report_path = save_report(
            html,
            config.REPORTS_DIR,
            report_date=report_day,
            prefix="ep_basic_digest",
        )
        logger.info("HTML report saved to %s", report_path)

        if send_email_flag:
            subject = basic_subject(
                report_day,
                article_count=len(new_articles),
                lookback_days=lookback_days,
            )
            ok = send_email(html, subject=subject)
            if not ok:
                logger.error("Email send failed; report is still on disk.")
                return 1
            logger.info("Email sent (%d papers)", len(new_articles))
        else:
            logger.info("--no-email set; skipped SMTP send.")

        # Mark processed only after a successful report build
        for article in new_articles:
            db.mark_processed(
                _BASIC_SOURCE,
                article.source_id,
                title=article.title,
            )
        logger.info("Marked %d article(s) as processed (basic DB)", len(new_articles))

    logger.info("Basic pipeline finished successfully.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI flags for the basic digest."""
    parser = argparse.ArgumentParser(
        description=(
            "EP PubMed basic digest (no OpenAI). "
            "Uses PubMed + optional email only."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Publication lookback window in days (default from .env / 7).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search/match only; do not save report, email, or mark processed.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Save HTML report but do not send email.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore dedupe and re-report all articles in the lookback window "
            "(useful after changing email recipients)."
        ),
    )
    parser.add_argument(
        "--print-schedule",
        action="store_true",
        help="Print weekly/daily Windows Task Scheduler + cron setup, then exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the basic (no-LLM) digest."""
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)
    lookback = args.lookback_days or config.LOOKBACK_DAYS

    if args.print_schedule:
        print_schedule_instructions(
            config.SCHEDULE_MODE,
            project_root=Path(__file__).resolve().parent.parent,
            module="main_basic",
            lookback_days=lookback,
        )
        return 0

    try:
        return run_basic_pipeline(
            lookback_days=lookback,
            send_email_flag=not args.no_email,
            dry_run=args.dry_run,
            force=args.force,
        )
    except Exception:
        logger.exception("Unhandled basic-pipeline failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
