"""Build and send the HTML competitive-intelligence email report."""

from __future__ import annotations

import logging
import smtplib
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Optional

from ep_monitor import config
from ep_monitor.models.article import Article, ArticleSummary
from ep_monitor.report_charts import charts_as_data_uris

logger = logging.getLogger(__name__)


def filter_high_impact(
    summaries: list[ArticleSummary],
    threshold: int = 7,
) -> list[ArticleSummary]:
    """Keep summaries with importance_score >= threshold, sorted descending."""
    filtered = [s for s in summaries if s.importance_score >= threshold]
    filtered.sort(key=lambda s: (-s.importance_score, s.title.casefold()))
    logger.info(
        "High-impact filter: %d / %d summaries (threshold=%d)",
        len(filtered),
        len(summaries),
        threshold,
    )
    return filtered


def _distribution(values: list[str]) -> list[tuple[str, int]]:
    """Return (label, count) pairs sorted by count desc then label."""
    counts = Counter(v.strip() or "Unknown" for v in values)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))


def _fmt_date(value: date | None) -> str:
    if value is None:
        return "Unknown"
    return value.isoformat()


def _bullets(items: list[str]) -> str:
    if not items:
        return "<p><em>No key findings provided.</em></p>"
    lis = "".join(f"<li>{escape(item)}</li>" for item in items)
    return f"<ul>{lis}</ul>"


def _score_badge(score: int) -> str:
    if score >= 9:
        color = "#b91c1c"
    elif score >= 7:
        color = "#c2410c"
    else:
        color = "#374151"
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:4px;'
        f'background:{color};color:#fff;font-weight:700;">{score}/10</span>'
    )


def _paper_section(summary: ArticleSummary) -> str:
    products = ", ".join(summary.matched_products) if summary.matched_products else "—"
    pubmed_link = summary.url or f"https://pubmed.ncbi.nlm.nih.gov/{summary.source_id}/"
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="margin:0 0 28px 0;border-collapse:collapse;border:1px solid #e5e7eb;">
      <tr>
        <td style="padding:18px 20px;background:#0f172a;color:#f8fafc;">
          <div style="font-size:18px;font-weight:700;line-height:1.35;">
            {escape(summary.title)}
          </div>
          <div style="margin-top:8px;font-size:13px;opacity:0.9;">
            {escape(summary.journal or "Unknown journal")}
            &nbsp;·&nbsp; {_fmt_date(summary.publication_date)}
            &nbsp;·&nbsp; PMID {escape(summary.source_id)}
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:16px 20px;background:#ffffff;color:#111827;font-size:14px;line-height:1.55;">
          <p style="margin:0 0 10px 0;">
            <strong>Technology:</strong> {escape(summary.technology)}
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <strong>Disease:</strong> {escape(summary.disease)}
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <strong>Company:</strong> {escape(summary.company)}
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <strong>Study type:</strong> {escape(summary.study_type)}
          </p>
          <p style="margin:0 0 10px 0;">
            <strong>Importance score:</strong> {_score_badge(summary.importance_score)}
            &nbsp;&nbsp;|&nbsp;&nbsp;
            <strong>Matched products:</strong> {escape(products)}
          </p>
          <h3 style="margin:18px 0 8px 0;font-size:15px;color:#0f172a;">Summary</h3>
          <p style="margin:0 0 12px 0;">{escape(summary.summary)}</p>
          <h3 style="margin:18px 0 8px 0;font-size:15px;color:#0f172a;">Key Findings</h3>
          {_bullets(summary.key_findings)}
          <h3 style="margin:18px 0 8px 0;font-size:15px;color:#0f172a;">Clinical Impact</h3>
          <p style="margin:0 0 12px 0;">{escape(summary.clinical_impact)}</p>
          <h3 style="margin:18px 0 8px 0;font-size:15px;color:#0f172a;">Competitive Intelligence</h3>
          <p style="margin:0 0 12px 0;">{escape(summary.competitive_intelligence)}</p>
          <p style="margin:16px 0 0 0;">
            <a href="{escape(pubmed_link, quote=True)}"
               style="color:#1d4ed8;font-weight:600;text-decoration:none;">
              View on PubMed →
            </a>
          </p>
        </td>
      </tr>
    </table>
    """.strip()


def _dist_table(title: str, rows: list[tuple[str, int]]) -> str:
    if not rows:
        body = "<tr><td colspan='2' style='padding:8px 0;color:#6b7280;'>None</td></tr>"
    else:
        body = "".join(
            f"<tr>"
            f"<td style='padding:6px 12px 6px 0;border-bottom:1px solid #e5e7eb;'>"
            f"{escape(label)}</td>"
            f"<td style='padding:6px 0;border-bottom:1px solid #e5e7eb;text-align:right;"
            f"font-weight:600;'>{count}</td>"
            f"</tr>"
            for label, count in rows
        )
    return f"""
    <div style="margin:0 0 16px 0;">
      <div style="font-weight:700;margin:0 0 8px 0;">{escape(title)}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;font-size:14px;">{body}</table>
    </div>
    """.strip()


def build_html_report(
    all_summaries: list[ArticleSummary],
    high_impact: list[ArticleSummary],
    report_date: date | None = None,
    total_found: int = 0,
) -> str:
    """Render a professional HTML email body.

    Includes per-paper sections plus footer stats (totals, technology
    distribution, company distribution).
    """
    report_day = report_date or date.today()
    total_found = total_found if total_found > 0 else len(all_summaries)
    tech_dist = _distribution([s.technology for s in all_summaries])
    company_dist = _distribution([s.company for s in all_summaries])

    if high_impact:
        papers_html = "\n".join(_paper_section(s) for s in high_impact)
    else:
        papers_html = (
            "<p style='padding:12px 0;color:#6b7280;'>"
            "No high-impact papers met the importance threshold for this period."
            "</p>"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>EP Competitive Intelligence Report</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="720" cellpadding="0" cellspacing="0"
               style="max-width:720px;width:100%;background:#ffffff;border-collapse:collapse;">
          <tr>
            <td style="padding:28px 24px 18px 24px;background:#111827;color:#f9fafb;">
              <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.8;">
                Biosense Webster · Competitive Intelligence
              </div>
              <div style="font-size:26px;font-weight:750;margin-top:8px;line-height:1.25;">
                EP Competitive Intelligence Report
              </div>
              <div style="margin-top:10px;font-size:14px;opacity:0.9;">
                {escape(report_day.isoformat())}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 24px 8px 24px;color:#111827;">
              <p style="margin:0 0 18px 0;font-size:14px;line-height:1.5;color:#374151;">
                High-impact cardiac electrophysiology publications related to
                competitor technologies and products. Only papers scoring
                <strong>≥ {config.IMPORTANCE_THRESHOLD}</strong> are listed below.
              </p>
              {papers_html}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 24px 28px 24px;color:#111827;">
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:8px 0 20px 0;"/>
              <div style="font-size:16px;font-weight:700;margin:0 0 14px 0;">
                Report Statistics
              </div>
              <p style="margin:0 0 14px 0;font-size:14px;">
                <strong>Total papers found:</strong> {total_found}<br/>
                <strong>Total summarized:</strong> {len(all_summaries)}<br/>
                <strong>Total high-impact papers:</strong> {len(high_impact)}
              </p>
              {_dist_table("Technology distribution", tech_dist)}
              {_dist_table("Company distribution", company_dist)}
              <p style="margin:20px 0 0 0;font-size:12px;color:#6b7280;">
                Generated automatically by EP Monitor · {escape(generated_at)}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()


def save_report(
    html: str,
    output_dir: Path | str,
    report_date: date | None = None,
    *,
    prefix: str = "ep_ci_report",
) -> Path:
    """Write HTML to ``reports/`` and return the path."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = report_date or date.today()
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    path = out_dir / f"{prefix}_{day.isoformat()}_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    logger.info("Saved HTML report to %s", path)
    return path


def default_subject(report_date: date | None = None, high_impact_count: int = 0) -> str:
    """Build a concise email subject line."""
    day = (report_date or date.today()).isoformat()
    return f"EP Competitive Intelligence Report — {day} ({high_impact_count} high-impact)"


def basic_subject(
    report_date: date | None = None,
    article_count: int = 0,
    *,
    lookback_days: int = 7,
    period_start: date | None = None,
    period_end: date | None = None,
) -> str:
    """Subject line for the basic PubMed listing report."""
    end = period_end or report_date or date.today()
    start = period_start or (end - timedelta(days=max(lookback_days, 1) - 1))
    window = _fmt_period(start, end)
    return (
        f"J&J EP Monitor — EP PubMed Digest ({window}) · {article_count} papers"
    )


def _fmt_period(start: date, end: date) -> str:
    """Human-readable inclusive date window, e.g. Jul 30 – Aug 5, 2026."""
    if start == end:
        return start.strftime("%b %d, %Y")
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b %d')} – {end.strftime('%d, %Y')}"
    if start.year == end.year:
        return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"


def _visual_overview_html(chart_uris: dict[str, str]) -> str:
    """Build the Visual overview section with embedded chart images."""
    if not chart_uris:
        return ""

    blocks: list[str] = []
    bubble = chart_uris.get("bubble_matrix")
    if bubble:
        blocks.append(
            f"""
            <div style="margin:0 0 18px 0;">
              <div style="font-size:13px;font-weight:700;color:#111827;margin:0 0 6px 0;">
                Company × technology matrix
              </div>
              <div style="font-size:12px;color:#6b7280;margin:0 0 10px 0;line-height:1.45;">
                Bubble size = number of papers. Technology is inferred from title/abstract/products
                (PFA, RF, Cryo, Mapping).
              </div>
              <img src="{bubble}" alt="Company by technology bubble matrix"
                   width="624"
                   style="display:block;width:100%;max-width:624px;height:auto;border:1px solid #e5e7eb;"/>
            </div>
            """.strip()
        )
    bar = chart_uris.get("company_bar")
    if bar:
        blocks.append(
            f"""
            <div style="margin:0 0 8px 0;">
              <div style="font-size:13px;font-weight:700;color:#111827;margin:0 0 6px 0;">
                Papers by company
              </div>
              <div style="font-size:12px;color:#6b7280;margin:0 0 10px 0;line-height:1.45;">
                Count of papers attributed to each competitor in this coverage window.
              </div>
              <img src="{bar}" alt="Papers by company bar chart"
                   width="624"
                   style="display:block;width:100%;max-width:624px;height:auto;border:1px solid #e5e7eb;"/>
            </div>
            """.strip()
        )

    if not blocks:
        return ""

    inner = "\n".join(blocks)
    return f"""
          <tr>
            <td style="padding:22px 28px 8px 28px;background:#ffffff;
                       border-bottom:1px solid #e5e7eb;">
              <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;
                          text-transform:uppercase;color:#9ca3af;margin:0 0 14px 0;">
                Visual overview
              </div>
              {inner}
            </td>
          </tr>
    """.strip()


def _truncate(text: str, limit: int = 500) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _company_chips(companies: list[str]) -> str:
    if not companies:
        return (
            '<span style="display:inline-block;padding:3px 10px;margin:0 6px 6px 0;'
            "background:#f3f4f6;color:#6b7280;font-size:12px;font-weight:600;"
            'border-radius:3px;">Unmatched</span>'
        )
    chips = []
    for name in companies:
        chips.append(
            f'<span style="display:inline-block;padding:3px 10px;margin:0 6px 6px 0;'
            f"background:#fce8ec;color:#9f1239;font-size:12px;font-weight:600;"
            f'border-radius:3px;">{escape(name)}</span>'
        )
    return "".join(chips)


def _basic_paper_section(article: Article, index: int) -> str:
    products = ", ".join(article.matched_products) if article.matched_products else "—"
    authors = ", ".join(article.authors[:8]) if article.authors else "Unknown"
    if article.authors and len(article.authors) > 8:
        authors += f" (+{len(article.authors) - 8} more)"
    pubmed_link = article.url or f"https://pubmed.ncbi.nlm.nih.gov/{article.source_id}/"
    abstract = _truncate(article.abstract, 480) or "No abstract available."
    chips = _company_chips(article.matched_companies)

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="margin:0 0 18px 0;border-collapse:collapse;
                  border:1px solid #e8eaed;">
      <tr>
        <td style="width:4px;background:#c8102e;font-size:0;line-height:0;">&nbsp;</td>
        <td style="padding:18px 20px;background:#ffffff;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.06em;
                      text-transform:uppercase;color:#9ca3af;margin:0 0 8px 0;">
            Paper {index}
          </div>
          <div style="font-size:16px;font-weight:700;line-height:1.4;color:#111827;
                      margin:0 0 10px 0;">
            <a href="{escape(pubmed_link, quote=True)}"
               style="color:#111827;text-decoration:none;">
              {escape(article.title)}
            </a>
          </div>
          <div style="margin:0 0 12px 0;">
            {chips}
          </div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="margin:0 0 12px 0;border-collapse:collapse;">
            <tr>
              <td style="padding:0 0 4px 0;font-size:13px;color:#4b5563;line-height:1.5;">
                <span style="color:#9ca3af;">Journal</span>&nbsp;
                {escape(article.journal or "Unknown")}
              </td>
            </tr>
            <tr>
              <td style="padding:0 0 4px 0;font-size:13px;color:#4b5563;line-height:1.5;">
                <span style="color:#9ca3af;">Published</span>&nbsp;
                {_fmt_date(article.publication_date)}
                &nbsp;&nbsp;·&nbsp;&nbsp;
                <span style="color:#9ca3af;">PMID</span>&nbsp;
                {escape(article.source_id)}
              </td>
            </tr>
            <tr>
              <td style="padding:0 0 4px 0;font-size:13px;color:#4b5563;line-height:1.5;">
                <span style="color:#9ca3af;">Products</span>&nbsp;
                {escape(products)}
              </td>
            </tr>
            <tr>
              <td style="padding:0;font-size:13px;color:#4b5563;line-height:1.5;">
                <span style="color:#9ca3af;">Authors</span>&nbsp;
                {escape(authors)}
              </td>
            </tr>
          </table>
          <div style="padding:12px 14px;background:#f9fafb;border-left:3px solid #e5e7eb;
                      font-size:13px;line-height:1.6;color:#374151;">
            {escape(abstract)}
          </div>
          <div style="margin-top:14px;">
            <a href="{escape(pubmed_link, quote=True)}"
               style="display:inline-block;padding:8px 14px;background:#c8102e;color:#ffffff;
                      font-size:12px;font-weight:700;text-decoration:none;border-radius:4px;">
              View on PubMed
            </a>
          </div>
        </td>
      </tr>
    </table>
    """.strip()


def build_basic_html_report(
    articles: list[Article],
    report_date: date | None = None,
    total_found: int = 0,
    *,
    lookback_days: int = 7,
    period_start: date | None = None,
    period_end: date | None = None,
) -> str:
    """Render a basic HTML digest (no LLM summaries)."""
    report_day = report_date or date.today()
    end = period_end or report_day
    days = max(int(lookback_days), 1)
    start = period_start or (end - timedelta(days=days - 1))
    period_label = _fmt_period(start, end)
    day_word = "day" if days == 1 else "days"
    total_found = total_found if total_found > 0 else len(articles)

    # Sort: competitor-tagged first, then by date/title
    ordered = sorted(
        articles,
        key=lambda a: (
            0 if a.matched_companies else 1,
            -(a.publication_date.toordinal() if a.publication_date else 0),
            a.title.casefold(),
        ),
    )

    company_values: list[str] = []
    for article in ordered:
        if article.matched_companies:
            company_values.extend(article.matched_companies)
        else:
            company_values.append("Unknown")
    company_dist = _distribution(company_values)

    if ordered:
        papers_html = "\n".join(
            _basic_paper_section(a, i) for i, a in enumerate(ordered, start=1)
        )
    else:
        papers_html = (
            "<p style='padding:16px 0;color:#6b7280;font-size:14px;'>"
            "No new PubMed articles found for this period."
            "</p>"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    attributed = sum(1 for a in ordered if a.matched_companies)

    top_companies = company_dist[:5]
    if top_companies:
        dist_bits = " · ".join(f"{escape(label)} ({count})" for label, count in top_companies)
    else:
        dist_bits = "—"

    chart_uris = charts_as_data_uris(ordered) if ordered else {}
    charts_html = _visual_overview_html(chart_uris)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>J&amp;J EP Monitor — EP PubMed Digest ({escape(period_label)})</title>
</head>
<body style="margin:0;padding:0;background:#e8ebf0;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#e8ebf0;">
    <tr>
      <td align="center" style="padding:28px 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0"
               style="max-width:680px;width:100%;border-collapse:collapse;">

          <!-- Header -->
          <tr>
            <td style="padding:0;background:#141414;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     style="border-collapse:collapse;">
                <tr>
                  <td style="height:4px;background:#c8102e;font-size:0;line-height:0;">
                    &nbsp;
                  </td>
                </tr>
                <tr>
                  <td style="padding:30px 28px 26px 28px;color:#ffffff;">
                    <div style="font-size:12px;font-weight:700;letter-spacing:0.16em;
                                text-transform:uppercase;color:#c8102e;">
                      J&amp;J EP Monitor &nbsp;·&nbsp; Biosense Webster
                    </div>
                    <div style="font-size:28px;font-weight:700;margin-top:12px;
                                line-height:1.2;letter-spacing:-0.02em;">
                      EP PubMed Digest
                    </div>
                    <div style="margin-top:14px;display:inline-block;padding:8px 12px;
                                background:#2a2a2a;font-size:13px;font-weight:600;
                                color:#f3f4f6;line-height:1.4;">
                      Coverage window &nbsp;|&nbsp; {escape(period_label)}
                      &nbsp;({days} {day_word})
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- What am I reading? -->
          <tr>
            <td style="padding:22px 28px;background:#fff8f8;border-bottom:1px solid #f0d4d8;">
              <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;
                          text-transform:uppercase;color:#c8102e;margin:0 0 8px 0;">
                What this email is
              </div>
              <p style="margin:0;font-size:14px;line-height:1.65;color:#1f2937;">
                A competitive-intelligence digest of
                <strong>new PubMed publications from the last {days} {day_word}</strong>
                ({escape(period_label)}) in cardiac electrophysiology.
                Papers mention competitor companies or devices
                (Boston Scientific, Medtronic, Abbott, MicroPort, AtriCure)
                related to ablation, mapping, and arrhythmia care.
                Each entry includes title, journal, date, matched company/product,
                authors, and an abstract preview — with a link to PubMed.
              </p>
            </td>
          </tr>

          <!-- Stats strip -->
          <tr>
            <td style="padding:0;background:#ffffff;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     style="border-collapse:collapse;border-bottom:1px solid #e5e7eb;">
                <tr>
                  <td width="33%" style="padding:18px 16px 18px 28px;vertical-align:top;">
                    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;
                                text-transform:uppercase;color:#9ca3af;">In this email</div>
                    <div style="font-size:22px;font-weight:700;color:#111827;margin-top:4px;">
                      {len(ordered)}
                    </div>
                    <div style="font-size:12px;color:#6b7280;margin-top:2px;">new papers</div>
                  </td>
                  <td width="33%" style="padding:18px 16px;vertical-align:top;
                                         border-left:1px solid #e5e7eb;">
                    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;
                                text-transform:uppercase;color:#9ca3af;">Competitor-tagged</div>
                    <div style="font-size:22px;font-weight:700;color:#111827;margin-top:4px;">
                      {attributed}
                    </div>
                    <div style="font-size:12px;color:#6b7280;margin-top:2px;">matched to a company</div>
                  </td>
                  <td width="34%" style="padding:18px 28px 18px 16px;vertical-align:top;
                                         border-left:1px solid #e5e7eb;">
                    <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;
                                text-transform:uppercase;color:#9ca3af;">PubMed hits</div>
                    <div style="font-size:22px;font-weight:700;color:#111827;margin-top:4px;">
                      {total_found}
                    </div>
                    <div style="font-size:12px;color:#6b7280;margin-top:2px;">in search window</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {charts_html}

          <!-- Papers -->
          <tr>
            <td style="padding:24px 28px 8px 28px;background:#ffffff;">
              <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;
                          text-transform:uppercase;color:#9ca3af;margin:0 0 6px 0;">
                Articles published {escape(period_label)}
              </div>
              <div style="font-size:13px;color:#6b7280;margin:0 0 16px 0;line-height:1.5;">
                Sorted with competitor-attributed papers first.
              </div>
              {papers_html}
            </td>
          </tr>

          <!-- Footer stats -->
          <tr>
            <td style="padding:8px 28px 28px 28px;background:#ffffff;">
              <div style="padding-top:20px;border-top:1px solid #e5e7eb;">
                <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;
                            text-transform:uppercase;color:#9ca3af;margin:0 0 10px 0;">
                  Company mix in this digest
                </div>
                <div style="font-size:13px;color:#4b5563;line-height:1.6;margin:0 0 14px 0;">
                  {dist_bits}
                </div>
                <p style="margin:22px 0 0 0;font-size:11px;color:#9ca3af;line-height:1.5;">
                  J&amp;J EP Monitor · Coverage {escape(period_label)} · Generated {escape(generated_at)}
                </p>
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()


def send_email(
    html_body: str,
    subject: str | None = None,
    *,
    recipients: Optional[list[str]] = None,
) -> bool:
    """Send the report via SMTP using BCC so recipients cannot see each other.

    The visible ``To`` header is set to the sender (or a generic undisclosed
    label). Real addresses are only in the SMTP envelope (and a ``Bcc``
    header that is stripped before delivery).
    """
    to_addrs = recipients if recipients is not None else list(config.EMAIL_TO)
    from_addr = (config.EMAIL_FROM or config.SMTP_USER or "").strip()
    user = (config.SMTP_USER or "").strip()
    password = (config.SMTP_PASSWORD or "").strip()
    host = config.SMTP_HOST
    port = int(config.SMTP_PORT)

    if not to_addrs:
        logger.error("EMAIL_TO is empty; cannot send report")
        return False
    if not from_addr:
        logger.error("EMAIL_FROM / SMTP_USER is empty; cannot send report")
        return False
    if not user or not password:
        logger.error("SMTP_USER / SMTP_PASSWORD missing; cannot send report")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or default_subject()
    msg["From"] = from_addr
    # Visible To is sender only so recipients cannot see each other.
    # Real addresses are delivered via the SMTP envelope (blind copy).
    msg["To"] = from_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        logger.info(
            "Sending report email (BCC) to %d recipient(s) via %s:%s",
            len(to_addrs),
            host,
            port,
        )
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            # Envelope recipients = blind list; they will not appear in To.
            server.sendmail(from_addr, to_addrs, msg.as_string())
        logger.info("Email sent successfully (BCC)")
        return True
    except Exception:
        logger.exception("Failed to send email report")
        return False
