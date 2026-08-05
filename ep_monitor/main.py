"""CLI entry point for the EP competitive intelligence pipeline.

Orchestrates: search → dedupe → company match → summarize → email.

Usage
-----
    python -m ep_monitor.main
    python -m ep_monitor.main --lookback-days 7 --dry-run
    python -m ep_monitor.main --no-email
    python -m ep_monitor.main --print-schedule
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from ep_monitor import config
from ep_monitor.company_matcher import match_articles
from ep_monitor.config import RuntimeConfig
from ep_monitor.database import ArticleDatabase
from ep_monitor.email_report import (
    build_html_report,
    default_subject,
    filter_high_impact,
    save_report,
    send_email,
)
from ep_monitor.openai_summary import summarize_articles
from ep_monitor.pubmed_search import search_pubmed
from ep_monitor.scheduler import print_schedule_instructions

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure root logging for the pipeline run."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def run_pipeline(cfg: RuntimeConfig) -> int:
    """Execute one full competitive-intelligence cycle.

    Returns:
        Process exit code (0 = success).
    """
    report_day = date.today()
    logger.info("=" * 60)
    logger.info("EP Competitive Intelligence Monitor — %s", report_day.isoformat())
    logger.info(
        "lookback_days=%s threshold=%s dry_run=%s send_email=%s",
        cfg.lookback_days,
        cfg.importance_threshold,
        cfg.dry_run,
        cfg.send_email,
    )
    logger.info("=" * 60)

    # --- Step 1: PubMed search -------------------------------------------------
    articles = search_pubmed(lookback_days=cfg.lookback_days)
    total_found = len(articles)
    logger.info("Step 1 complete: fetched %d article(s) from PubMed", total_found)

    # --- Step 2: Dedupe against SQLite ----------------------------------------
    with ArticleDatabase(config.DATABASE_PATH) as db:
        new_articles = db.filter_new(articles)
        logger.info("Step 2 complete: %d new article(s) after dedupe", len(new_articles))

        if not new_articles:
            logger.info("Nothing new to process. Exiting successfully.")
            return 0

        # --- Step 3: Company / product attribution ----------------------------
        match_articles(new_articles, competitors_only=True)
        attributed = sum(1 for a in new_articles if a.matched_companies)
        logger.info(
            "Step 3 complete: attributed %d / %d article(s) to competitors",
            attributed,
            len(new_articles),
        )

        if cfg.dry_run:
            logger.info("Dry-run mode: skipping OpenAI summarization and email.")
            for article in new_articles:
                logger.info(
                    "  would summarize PMID=%s companies=%s products=%s | %s",
                    article.source_id,
                    article.matched_companies or ["Unknown"],
                    article.matched_products or [],
                    article.title[:100],
                )
            return 0

        # --- Step 4: OpenAI summarization -------------------------------------
        summaries = summarize_articles(new_articles)
        logger.info("Step 4 complete: %d summary(ies) generated", len(summaries))

        # --- Step 5: Persist --------------------------------------------------
        for summary in summaries:
            db.save_summary(summary, source="pubmed")
        logger.info("Step 5 complete: persisted %d summary(ies)", len(summaries))

        if not summaries:
            logger.warning("No summaries produced; skipping report/email.")
            return 0

        # --- Step 6: Report + email -------------------------------------------
        high_impact = filter_high_impact(
            summaries,
            threshold=cfg.importance_threshold,
        )
        html = build_html_report(
            all_summaries=summaries,
            high_impact=high_impact,
            report_date=report_day,
            total_found=total_found,
        )
        report_path = save_report(html, config.REPORTS_DIR, report_date=report_day)
        logger.info("Step 6: HTML report saved to %s", report_path)

        if cfg.send_email:
            subject = default_subject(report_day, high_impact_count=len(high_impact))
            ok = send_email(html, subject=subject)
            if not ok:
                logger.error("Email send failed; report is still available on disk.")
                return 1
            logger.info("Step 6 complete: email sent (%d high-impact)", len(high_impact))
        else:
            logger.info("Step 6 complete: --no-email set; skipped SMTP send.")

    logger.info("Pipeline finished successfully.")
    return 0


def parse_args(argv: list[str] | None = None) -> tuple[RuntimeConfig, argparse.Namespace]:
    """Parse CLI flags into a ``RuntimeConfig`` plus raw namespace."""
    parser = argparse.ArgumentParser(
        description="EP Competitive Intelligence Monitor (Biosense Webster)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Publication lookback window in days (default from env/config).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Minimum importance score for email inclusion (default 7).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run search/dedupe/match only; skip OpenAI and email.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Generate/save HTML report but do not send email.",
    )
    parser.add_argument(
        "--print-schedule",
        action="store_true",
        help="Print cron / Windows Task Scheduler setup commands and exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    cfg = RuntimeConfig(
        lookback_days=args.lookback_days or config.LOOKBACK_DAYS,
        importance_threshold=args.threshold or config.IMPORTANCE_THRESHOLD,
        schedule_mode=config.SCHEDULE_MODE,
        dry_run=args.dry_run,
        send_email=not args.no_email,
    )
    return cfg, args


def main(argv: list[str] | None = None) -> int:
    """Program entry point."""
    cfg, args = parse_args(argv)
    try:
        if args.print_schedule:
            print_schedule_instructions(
                cfg.schedule_mode,
                project_root=config.REPO_ROOT,
                module="main",
            )
            return 0
        return run_pipeline(cfg)
    except Exception:
        logger.exception("Unhandled pipeline failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
