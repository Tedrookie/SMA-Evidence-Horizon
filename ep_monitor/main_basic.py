"""Basic PubMed digest — no LLM / OpenAI required.

Fetches recent competitor papers using the surveillance playbook,
stores full articles for LLM handoff, and emails a J&J-styled digest.

Usage
-----
    python -m ep_monitor.main_basic
    python -m ep_monitor.main_basic --lookback-days 7 --no-email
    python -m ep_monitor.main_basic --dry-run
    python -m ep_monitor.main_basic --force
    python -m ep_monitor.main_basic --print-schedule
    python -m ep_monitor.console
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from ep_monitor import config
from ep_monitor import playbook as pb
from ep_monitor.company_matcher import match_articles
from ep_monitor.database import ArticleDatabase
from ep_monitor.email_report import (
    basic_subject,
    build_basic_html_report,
    save_report,
    send_email,
)
from ep_monitor.export_excel import articles_to_rows, export_articles_to_excel
from ep_monitor.pubmed_search import search_pubmed
from ep_monitor.scheduler import print_schedule_instructions

logger = logging.getLogger(__name__)

_BASIC_SOURCE = "pubmed_basic"
_BASIC_DB = config.DATA_DIR / "basic_processed.db"
_LIBRARY_DB = config.ARTICLES_DB_PATH


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
    lookback_days: int | None = None,
    send_email_flag: bool,
    dry_run: bool,
    force: bool = False,
    playbook: dict | None = None,
) -> int:
    """Run PubMed → match → library save → Excel → HTML email (no LLM)."""
    book = playbook if playbook is not None else pb.load_playbook()
    days = lookback_days if lookback_days is not None else pb.lookback_days(book)
    domain_ids = [d.get("id", "ep") for d in pb.enabled_domains(book)]
    domain_id = ",".join(str(x) for x in domain_ids) if domain_ids else "ep"
    cmap = pb.company_product_map(book, include_own=False)

    report_day = date.today()
    logger.info("=" * 60)
    logger.info("%s — EP PubMed Digest — %s", pb.product_name(book), report_day.isoformat())
    logger.info(
        "lookback_days=%s dry_run=%s send_email=%s force=%s domains=%s",
        days,
        dry_run,
        send_email_flag,
        force,
        domain_ids,
    )
    logger.info("=" * 60)

    articles = search_pubmed(lookback_days=days)
    total_found = len(articles)
    logger.info("Fetched %d article(s) from PubMed", total_found)

    for article in articles:
        article.source = _BASIC_SOURCE

    with ArticleDatabase(_BASIC_DB) as dedupe_db, ArticleDatabase(_LIBRARY_DB) as library_db:
        if force:
            new_articles = articles
            logger.info(
                "Force mode: skipping dedupe; reporting all %d fetched article(s)",
                len(new_articles),
            )
        else:
            new_articles = dedupe_db.filter_new(articles)
            logger.info("%d new article(s) after dedupe", len(new_articles))

        if not new_articles:
            logger.info("Nothing new to report. Exiting successfully.")
            return 0

        match_articles(new_articles, company_map=cmap, competitors_only=True)
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

        # Persist full articles for later LLM / Excel handoff
        library_db.upsert_articles(new_articles, domain_id=domain_id)
        excel_path = export_articles_to_excel(
            articles_to_rows(new_articles, domain_id=domain_id),
            report_date=report_day,
        )
        logger.info("Excel snapshot: %s", excel_path)

        html = build_basic_html_report(
            new_articles,
            report_date=report_day,
            total_found=total_found,
            lookback_days=days,
            playbook=book,
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
                lookback_days=days,
                playbook=book,
            )
            recipients = pb.recipient_emails(book)
            ok = send_email(html, subject=subject, recipients=recipients or None)
            if not ok:
                logger.error("Email send failed; report is still on disk.")
                return 1
            logger.info("Email sent (%d papers) to %d recipient(s)", len(new_articles), len(recipients or config.EMAIL_TO))
        else:
            logger.info("--no-email set; skipped SMTP send.")

        for article in new_articles:
            dedupe_db.mark_processed(
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
            "Uses playbook keywords + optional email."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Publication lookback window in days (default from playbook / .env).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search/match only; do not save report, email, or mark processed.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Save HTML report / Excel but do not send email.",
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
    book = pb.load_playbook()
    lookback = args.lookback_days or pb.lookback_days(book)

    if args.print_schedule:
        sched = book.get("schedule") or {}
        print_schedule_instructions(
            str(sched.get("mode") or config.SCHEDULE_MODE),
            project_root=Path(__file__).resolve().parent.parent,
            module="main_basic",
            lookback_days=lookback,
            hour=int(sched.get("hour", 8)),
            minute=int(sched.get("minute", 0)),
            weekday=str(sched.get("weekday") or "monday"),
        )
        return 0

    try:
        return run_basic_pipeline(
            lookback_days=lookback,
            send_email_flag=not args.no_email,
            dry_run=args.dry_run,
            force=args.force,
            playbook=book,
        )
    except Exception:
        logger.exception("Unhandled basic-pipeline failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
