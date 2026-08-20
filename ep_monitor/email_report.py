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
                Generated automatically by Evidence Horizon · {escape(generated_at)}
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
    playbook: dict | None = None,
) -> str:
    """Subject line for the Evidence Horizon digest."""
    from ep_monitor import playbook as pb

    end = period_end or report_date or date.today()
    start = period_start or (end - timedelta(days=max(lookback_days, 1) - 1))
    window = _fmt_period(start, end)
    name = pb.product_name(playbook)
    phrase = pb.digest_title(playbook)
    label = f"{name}: {phrase}" if phrase else name
    return f"J&J News: SMA {label} — {window} ({article_count} papers)"



def _fmt_mdy(day: date) -> str:
    """e.g. August 11, 2026 (no leading zero on the day)."""
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def _fmt_period(start: date, end: date) -> str:
    """Human-readable inclusive date window, e.g. August 11, 2026 to August 18, 2026."""
    if start == end:
        return _fmt_mdy(start)
    return f"{_fmt_mdy(start)} to {_fmt_mdy(end)}"


def _visual_overview_html(chart_uris: dict[str, str]) -> str:
    """Build the Visual overview section with embedded chart images."""
    if not chart_uris:
        return ""

    blocks: list[str] = []
    bubble = chart_uris.get("bubble_matrix")
    if bubble:
        blocks.append(
            f"""
            <div style="margin:0 0 20px 0;">
              <div style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 6px 0;
                          font-family:Arial,Helvetica,sans-serif;">
                Company × technology matrix
              </div>
              <div style="font-size:13px;color:#555555;margin:0 0 12px 0;line-height:1.5;
                          font-family:Arial,Helvetica,sans-serif;">
                Bubble size = number of papers. Technology is inferred from title/abstract/products.
              </div>
              <img src="{bubble}" alt="Company by technology bubble matrix"
                   width="624"
                   style="display:block;width:100%;max-width:624px;height:auto;"/>
            </div>
            """.strip()
        )
    bar = chart_uris.get("company_bar")
    if bar:
        blocks.append(
            f"""
            <div style="margin:0 0 8px 0;">
              <div style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 6px 0;
                          font-family:Arial,Helvetica,sans-serif;">
                Papers by company
              </div>
              <div style="font-size:13px;color:#555555;margin:0 0 12px 0;line-height:1.5;
                          font-family:Arial,Helvetica,sans-serif;">
                Count of papers attributed to each competitor in this coverage window.
              </div>
              <img src="{bar}" alt="Papers by company bar chart"
                   width="624"
                   style="display:block;width:100%;max-width:624px;height:auto;"/>
            </div>
            """.strip()
        )

    if not blocks:
        return ""

    inner = "\n".join(blocks)
    return f"""
          <tr>
            <td style="padding:28px 32px 12px 32px;background:#ffffff;">
              <div style="font-size:12px;font-weight:700;letter-spacing:0.12em;
                          text-transform:uppercase;color:#c8102e;margin:0 0 16px 0;
                          font-family:Arial,Helvetica,sans-serif;">
                Visual overview
              </div>
              {inner}
            </td>
          </tr>
    """.strip()


def _full_abstract(text: str) -> str:
    """Normalize whitespace but keep the full abstract (no truncation)."""
    cleaned = " ".join((text or "").split())
    return cleaned or "No abstract available."


def _is_jj_company(name: str) -> bool:
    n = (name or "").casefold()
    markers = (
        "johnson",
        "j&j",
        "jnj",
        "biosense",
        "ethicon",
        "cerenovus",
        "ottava",
        "monarch",
    )
    return any(m in n for m in markers)


def _shorten_affiliation(raw: str, *, limit: int = 160) -> str:
    aff = " ".join(str(raw or "").split())
    if len(aff) > limit:
        return aff[: limit - 1].rstrip() + "…"
    return aff


def _author_institute_block(article: Article) -> str:
    """First / last author + institute, each on its own line."""
    authors = article.authors or []
    meta = article.raw_metadata or {}
    affs = meta.get("affiliations") or []
    if not isinstance(affs, list):
        affs = []

    first_name = authors[0] if authors else "Unknown"
    last_name = authors[-1] if authors else "Unknown"
    if len(authors) == 1:
        last_name = first_name

    first_aff = meta.get("first_author_affiliation") or (affs[0] if affs else "")
    last_aff = meta.get("last_author_affiliation") or (affs[-1] if affs else "")
    first_aff = _shorten_affiliation(str(first_aff)) if first_aff else "—"
    last_aff = _shorten_affiliation(str(last_aff)) if last_aff else "—"

    return (
        f"First Author and Institute: {escape(first_name)}; {escape(first_aff)}<br/>"
        f"Last Author and Institute: {escape(last_name)}; {escape(last_aff)}"
    )


def _products_used_line(article: Article, playbook: dict | None = None) -> str:
    """e.g. SOFIA from MicroVention, EMBOTRAP from Johnson & Johnson."""
    from ep_monitor import playbook as pb

    companies = list(article.matched_companies or [])
    products = list(article.matched_products or [])
    if not companies and not products:
        return "No products or companies found from the playbook"

    company_map = pb.company_product_map(playbook, include_own=True)
    # product (casefold) -> preferred company display name
    product_owner: dict[str, str] = {}
    for company, kws in company_map.items():
        for kw in kws or []:
            key = str(kw).casefold().strip()
            if key and key not in product_owner:
                product_owner[key] = company

    bits: list[str] = []
    used_companies: set[str] = set()
    for product in products:
        owner = product_owner.get(product.casefold().strip())
        if owner:
            bits.append(f"{product} from {owner}")
            used_companies.add(owner.casefold())
        else:
            bits.append(product)

    for company in companies:
        if company.casefold() not in used_companies:
            bits.append(company)
            used_companies.add(company.casefold())

    return ", ".join(bits) if bits else "No products or companies found from the playbook"


def _domain_full_name(name: str) -> str:
    """Prefer full domain labels without short parenthetical codes."""
    import re

    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).strip()
    return cleaned or str(name or "").strip()


def _basic_paper_section(
    article: Article,
    index: int,
    total: int,
    *,
    playbook: dict | None = None,
) -> str:
    """Evidence Horizon article card (Paper i/N)."""
    pubmed_link = article.url or f"https://pubmed.ncbi.nlm.nih.gov/{article.source_id}/"
    abstract = _full_abstract(article.abstract)
    author_block = _author_institute_block(article)
    products_line = _products_used_line(article, playbook)

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="margin:0 0 32px 0;border-collapse:collapse;">
      <tr>
        <td style="padding:0 0 8px 0;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;
                      text-transform:uppercase;color:#c8102e;
                      font-family:Arial,Helvetica,sans-serif;">
            Paper {index}/{total}
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:0 0 10px 0;">
          <div style="font-size:22px;font-weight:700;line-height:1.3;color:#1a1a1a;
                      font-family:Arial,Helvetica,sans-serif;">
            <a href="{escape(pubmed_link, quote=True)}"
               style="color:#1a1a1a;text-decoration:none;">
              {escape(article.title)}
            </a>
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:0 0 10px 0;font-size:13px;color:#555555;line-height:1.65;
                   font-family:Arial,Helvetica,sans-serif;">
          {escape(article.journal or "Unknown journal")}
          &nbsp;·&nbsp; {_fmt_date(article.publication_date)}
          &nbsp;·&nbsp; PMID {escape(article.source_id)}
          <br/><br/>
          {author_block}
          <br/><br/>
          <strong>Products Used (from Companies):</strong>
          {escape(products_line)}
        </td>
      </tr>
      <tr>
        <td style="padding:8px 0 6px 0;">
          <div style="font-size:12px;font-weight:700;letter-spacing:0.08em;
                      text-transform:uppercase;color:#888888;
                      font-family:Arial,Helvetica,sans-serif;">
            Abstract
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:0 0 12px 0;font-size:14px;line-height:1.65;color:#333333;
                   font-family:Arial,Helvetica,sans-serif;">
          {escape(abstract)}
        </td>
      </tr>
      <tr>
        <td style="padding:0;">
          <a href="{escape(pubmed_link, quote=True)}"
             style="color:#c8102e;font-size:14px;font-weight:700;text-decoration:none;
                    font-family:Arial,Helvetica,sans-serif;">
            View full manuscript on PubMed →
          </a>
        </td>
      </tr>
    </table>
    """.strip()


def _jj_news_masthead_html(
    *,
    digest_phrase: str,
    tagline: str,
) -> str:
    """Simplified masthead: slogan + SMA Evidence Horizon: {phrase}."""
    headline = "SMA Evidence Horizon"
    if digest_phrase:
        headline = f"SMA Evidence Horizon: {digest_phrase}"
    return f"""
          <tr>
            <td style="padding:28px 32px 18px 32px;background:#ffffff;">
              <div style="font-family:Georgia,'Times New Roman',Times,serif;
                          font-size:13px;font-style:italic;color:#4a4a4a;
                          margin:0 0 16px 0;line-height:1.4;">
                {escape(tagline)}
              </div>
              <div style="font-family:Georgia,'Times New Roman',Times,serif;
                          font-size:26px;font-weight:700;line-height:1.25;
                          color:#c8102e;margin:0 0 12px 0;">
                {escape(headline)}
              </div>
              <div style="border-bottom:3px solid #c8102e;font-size:0;line-height:0;">
                &nbsp;
              </div>
            </td>
          </tr>
    """.strip()


def build_basic_html_report(
    articles: list[Article],
    report_date: date | None = None,
    total_found: int = 0,
    *,
    lookback_days: int = 7,
    period_start: date | None = None,
    period_end: date | None = None,
    playbook: dict | None = None,
) -> str:
    """Render Evidence Horizon HTML digest (no charts)."""
    from ep_monitor import playbook as pb

    book = playbook if playbook is not None else pb.load_playbook()
    tag = pb.tagline(book)
    phrase = pb.digest_title(book)
    domain_names = ", ".join(
        _domain_full_name(str(d.get("name") or d.get("id")))
        for d in pb.enabled_domains(book)
    ) or "Selected domains"

    report_day = report_date or date.today()
    end = period_end or report_day
    days = max(int(lookback_days), 1)
    start = period_start or (end - timedelta(days=days - 1))
    period_label = _fmt_period(start, end)

    ordered = sorted(
        articles,
        key=lambda a: (
            0 if a.matched_companies else 1,
            -(a.publication_date.toordinal() if a.publication_date else 0),
            a.title.casefold(),
        ),
    )
    total_papers = len(ordered)
    jj_count = sum(
        1
        for a in ordered
        if any(_is_jj_company(c) for c in (a.matched_companies or []))
    )
    other_count = sum(
        1
        for a in ordered
        if any(not _is_jj_company(c) for c in (a.matched_companies or []))
    )

    if ordered:
        papers_html = "\n".join(
            _basic_paper_section(a, i, total_papers, playbook=book)
            for i, a in enumerate(ordered, start=1)
        )
    else:
        papers_html = (
            "<p style='padding:16px 0;color:#666666;font-size:14px;"
            "font-family:Arial,Helvetica,sans-serif;'>"
            "No new PubMed articles found for this period."
            "</p>"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page_title = (
        f"SMA Evidence Horizon: {phrase}" if phrase else "SMA Evidence Horizon"
    )
    masthead = _jj_news_masthead_html(
        digest_phrase=phrase,
        tagline=tag,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{escape(page_title)} ({escape(period_label)})</title>
</head>
<body style="margin:0;padding:0;background:#f2f2f2;
             font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f2f2f2;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0"
               style="max-width:680px;width:100%;border-collapse:collapse;
                      background:#ffffff;">

          {masthead}

          <tr>
            <td style="padding:8px 32px 22px 32px;background:#ffffff;">
              <div style="font-size:12px;font-weight:700;letter-spacing:0.12em;
                          text-transform:uppercase;color:#c8102e;margin:0 0 12px 0;">
                Summary
              </div>
              <div style="font-size:15px;color:#333333;line-height:1.85;
                          font-family:Arial,Helvetica,sans-serif;">
                Domain: <strong>{escape(domain_names)}</strong><br/>
                Articles Published: <strong>{escape(period_label)}</strong><br/>
                Papers in this digest: <strong>{total_papers}</strong><br/>
                Johnson &amp; Johnson products used: <strong>{jj_count}</strong><br/>
                Other companies products used: <strong>{other_count}</strong>
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:8px 32px 8px 32px;background:#ffffff;">
              {papers_html}
            </td>
          </tr>

          <tr>
            <td style="padding:24px 32px 32px 32px;background:#ffffff;">
              <p style="margin:0;font-size:12px;color:#666666;line-height:1.7;
                         font-family:Arial,Helvetica,sans-serif;">
                SMA Evidence Horizon<br/>
                For any questions, please contact:
                <a href="mailto:RA-EvidenceHorizon@ITS.JNJ.com"
                   style="color:#c8102e;text-decoration:none;">
                  RA-EvidenceHorizon@ITS.JNJ.com
                </a><br/>
                Generated {escape(generated_at)}
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


def send_email(
    html_body: str,
    subject: str | None = None,
    *,
    recipients: Optional[list[str]] = None,
) -> bool:
    """Send the report via SMTP using BCC so recipients cannot see each other.

    The visible ``To`` header is set to the sender. Real addresses are only
    in the SMTP envelope (blind copy). Recipients default to playbook, then
    ``EMAIL_TO`` in ``.env``.
    """
    from ep_monitor import playbook as pb

    if recipients is not None:
        to_addrs = list(recipients)
    else:
        to_addrs = pb.recipient_emails()
    from_addr = (config.EMAIL_FROM or config.SMTP_USER or "").strip()
    user = (config.SMTP_USER or "").strip()
    password = (config.SMTP_PASSWORD or "").strip()
    host = config.SMTP_HOST
    port = int(config.SMTP_PORT)

    if not to_addrs:
        logger.error("No recipients in playbook or EMAIL_TO; cannot send report")
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
