"""Parse raw Entrez / Medline records into ``Article`` models.

Supports two common Biopython shapes so ``pubmed_search`` can use either:

1. **Medline text** dicts from ``Bio.Medline.parse``
   (keys: ``PMID``, ``TI``, ``AB``, ``AU``, ``JT``, ``DP``, ``AD``, …)
2. **PubMed XML** dicts from ``Bio.Entrez.read`` → ``PubmedArticle``
   (nested under ``MedlineCitation`` / ``Article``)

Missing abstracts are allowed (empty string). Records without PMID or title
are skipped and logged.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from ep_monitor.models.article import Article

logger = logging.getLogger(__name__)

PUBMED_URL_TEMPLATE = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

_MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _as_str(value: Any) -> str:
    """Coerce Entrez StringElement / bytes / None into a plain str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _first_str(value: Any) -> str:
    """Return the first string from a scalar or sequence."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return _as_str(value[0]) if value else ""
    return _as_str(value)


def _is_medline_text(record: Mapping[str, Any]) -> bool:
    """Heuristic: Medline text records expose top-level ``PMID`` / ``TI``."""
    return "PMID" in record or ("TI" in record and "MedlineCitation" not in record)


def _is_pubmed_xml(record: Mapping[str, Any]) -> bool:
    """Heuristic: XML records nest content under ``MedlineCitation``."""
    return "MedlineCitation" in record


def parse_medline_date(raw: str | None) -> Optional[date]:
    """Parse Medline ``DP`` / PubDate-like strings into a ``date``.

    Accepted examples: ``2024``, ``2024 Jan``, ``2024 Jan 15``,
    ``2024-01-15``, ``Jan 2024``. Missing month/day default to 1.
    """
    if not raw:
        return None
    text = _as_str(raw)
    if not text:
        return None

    # ISO-ish: 2024-01-15 or 2024/01/15
    iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    # Year first: 2024 Jan 15 | 2024 Jan | 2024
    year_first = re.match(
        r"^(\d{4})(?:\s+([A-Za-z]{3,9}))?(?:\s+(\d{1,2}))?$",
        text,
    )
    if year_first:
        year = int(year_first.group(1))
        month = _MONTH_MAP.get((year_first.group(2) or "jan").lower(), 1)
        day = int(year_first.group(3) or 1)
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 1)

    # Month first: Jan 2024 | January 15, 2024
    month_first = re.match(
        r"^([A-Za-z]{3,9})\s+(\d{1,2})?,?\s*(\d{4})$",
        text,
    )
    if month_first:
        month = _MONTH_MAP.get(month_first.group(1).lower())
        year = int(month_first.group(3))
        day = int(month_first.group(2) or 1)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return date(year, month, 1)

    # Last resort: leading 4-digit year
    year_only = re.search(r"(19|20)\d{2}", text)
    if year_only:
        try:
            return date(int(year_only.group(0)), 1, 1)
        except ValueError:
            return None

    logger.debug("Could not parse publication date: %r", text)
    return None


def _parse_xml_pub_date(pub_date: Mapping[str, Any] | None) -> Optional[date]:
    """Parse XML ``PubDate`` / ``ArticleDate`` dicts (Year/Month/Day/MedlineDate)."""
    if not pub_date:
        return None

    medline_date = pub_date.get("MedlineDate")
    if medline_date:
        return parse_medline_date(_as_str(medline_date))

    year_raw = _as_str(pub_date.get("Year"))
    if not year_raw.isdigit():
        return None
    year = int(year_raw)

    month_raw = _as_str(pub_date.get("Month")) or "1"
    if month_raw.isdigit():
        month = int(month_raw)
    else:
        month_key = month_raw.lower()
        month = _MONTH_MAP.get(month_key) or _MONTH_MAP.get(month_key[:3], 1)

    day_raw = _as_str(pub_date.get("Day")) or "1"
    day = int(day_raw) if day_raw.isdigit() else 1

    try:
        return date(year, month, day)
    except ValueError:
        try:
            return date(year, month, 1)
        except ValueError:
            return date(year, 1, 1)


def pubmed_url(pmid: str) -> str:
    """Build the canonical PubMed URL for a PMID."""
    return PUBMED_URL_TEMPLATE.format(pmid=pmid.strip())


# ---------------------------------------------------------------------------
# Medline text format
# ---------------------------------------------------------------------------


def _parse_medline_text(record: Mapping[str, Any]) -> Article | None:
    pmid = _first_str(record.get("PMID"))
    title = _as_str(record.get("TI"))
    if not pmid or not title:
        logger.warning("Skipping Medline record missing PMID/title: %r", record.get("PMID"))
        return None

    abstract = _as_str(record.get("AB"))
    journal = _as_str(record.get("JT")) or _as_str(record.get("TA")) or None
    authors_raw = record.get("FAU") or record.get("AU") or []
    if isinstance(authors_raw, str):
        authors = [authors_raw] if authors_raw.strip() else []
    else:
        authors = [_as_str(a) for a in authors_raw if _as_str(a)]

    affiliations_raw = record.get("AD") or []
    if isinstance(affiliations_raw, str):
        affiliations = [affiliations_raw] if affiliations_raw.strip() else []
    else:
        affiliations = [_as_str(a) for a in affiliations_raw if _as_str(a)]

    pub_date = parse_medline_date(_as_str(record.get("DP")) or _as_str(record.get("EDAT")))

    return Article(
        source_id=pmid,
        source="pubmed",
        title=title.rstrip("."),
        abstract=abstract,
        journal=journal,
        publication_date=pub_date,
        authors=authors,
        url=pubmed_url(pmid),
        raw_metadata={
            "format": "medline_text",
            "affiliations": affiliations,
            "publication_types": (
                list(record["PT"])
                if isinstance(record.get("PT"), list)
                else ([record["PT"]] if record.get("PT") else [])
            ),
            "doi": _extract_doi_from_medline(record),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
    )


def _extract_doi_from_medline(record: Mapping[str, Any]) -> Optional[str]:
    """Pull a DOI from Medline ``LID`` / ``AID`` fields when present."""
    for key in ("LID", "AID"):
        values = record.get(key) or []
        if isinstance(values, str):
            values = [values]
        for item in values:
            text = _as_str(item)
            if "doi" in text.lower():
                # e.g. "10.1000/xyz [doi]"
                doi = text.split("[", 1)[0].strip()
                return doi or None
            if text.lower().startswith("10."):
                return text
    return None


# ---------------------------------------------------------------------------
# PubMed XML format
# ---------------------------------------------------------------------------


def _xml_abstract(article: Mapping[str, Any]) -> str:
    abstract = article.get("Abstract") or {}
    texts = abstract.get("AbstractText")
    if texts is None:
        return ""
    if not isinstance(texts, (list, tuple)):
        return _as_str(texts)

    parts: list[str] = []
    for block in texts:
        label = ""
        # Biopython StringElement may expose attributes via .attributes
        attrs = getattr(block, "attributes", None) or {}
        if isinstance(attrs, dict):
            label = _as_str(attrs.get("Label"))
        body = _as_str(block)
        if label and body:
            parts.append(f"{label}: {body}")
        elif body:
            parts.append(body)
    return "\n\n".join(parts)


def _xml_authors(article: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (author display names, affiliation strings)."""
    author_list = article.get("AuthorList") or []
    authors: list[str] = []
    affiliations: list[str] = []

    for author in author_list:
        if not isinstance(author, Mapping):
            continue
        collective = _as_str(author.get("CollectiveName"))
        if collective:
            authors.append(collective)
        else:
            last = _as_str(author.get("LastName"))
            fore = _as_str(author.get("ForeName") or author.get("Initials"))
            if last and fore:
                authors.append(f"{last}, {fore}")
            elif last:
                authors.append(last)

        for info in author.get("AffiliationInfo") or []:
            if isinstance(info, Mapping):
                aff = _as_str(info.get("Affiliation"))
                if aff and aff not in affiliations:
                    affiliations.append(aff)

    return authors, affiliations


def _xml_publication_date(article: Mapping[str, Any]) -> Optional[date]:
    journal = article.get("Journal") or {}
    issue = journal.get("JournalIssue") or {}
    pub_date = _parse_xml_pub_date(issue.get("PubDate"))
    if pub_date:
        return pub_date

    # Electronic ArticleDate list
    article_dates = article.get("ArticleDate") or []
    if isinstance(article_dates, Mapping):
        article_dates = [article_dates]
    for entry in article_dates:
        if isinstance(entry, Mapping):
            parsed = _parse_xml_pub_date(entry)
            if parsed:
                return parsed

    return None


def _extract_doi_from_xml(article: Mapping[str, Any]) -> Optional[str]:
    id_list = article.get("ELocationID") or []
    if isinstance(id_list, Mapping):
        id_list = [id_list]
    if not isinstance(id_list, (list, tuple)):
        id_list = [id_list]
    for item in id_list:
        text = _as_str(item)
        attrs = getattr(item, "attributes", None) or {}
        id_type = _as_str(attrs.get("EIdType")).lower() if isinstance(attrs, dict) else ""
        if id_type == "doi" or text.lower().startswith("10."):
            return text or None
    return None


def _parse_pubmed_xml(record: Mapping[str, Any]) -> Article | None:
    citation = record.get("MedlineCitation") or {}
    article = citation.get("Article") or {}

    pmid = _first_str(citation.get("PMID"))
    title = _as_str(article.get("ArticleTitle"))
    if not pmid or not title:
        logger.warning("Skipping PubmedArticle missing PMID/title: %r", pmid)
        return None

    journal_info = article.get("Journal") or {}
    journal = (
        _as_str(journal_info.get("Title"))
        or _as_str(journal_info.get("ISOAbbreviation"))
        or None
    )
    authors, affiliations = _xml_authors(article)
    pub_date = _xml_publication_date(article)

    return Article(
        source_id=pmid,
        source="pubmed",
        title=title.rstrip("."),
        abstract=_xml_abstract(article),
        journal=journal,
        publication_date=pub_date,
        authors=authors,
        url=pubmed_url(pmid),
        raw_metadata={
            "format": "pubmed_xml",
            "affiliations": affiliations,
            "publication_types": _xml_publication_types(citation),
            "doi": _extract_doi_from_xml(article),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
        },
    )


def _xml_publication_types(citation: Mapping[str, Any]) -> list[str]:
    article = citation.get("Article") or {}
    types = article.get("PublicationTypeList") or []
    result: list[str] = []
    for item in types:
        text = _as_str(item)
        if text:
            result.append(text)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_pubmed_record(record: Mapping[str, Any] | Any) -> Article | None:
    """Convert one Entrez / Medline record into an ``Article``.

    Args:
        record: Single Medline text dict or PubmedArticle XML dict.

    Returns:
        Normalized ``Article``, or ``None`` if the record is unusable.
    """
    if record is None:
        return None
    if not isinstance(record, Mapping):
        logger.warning("Unexpected record type: %s", type(record).__name__)
        return None

    try:
        if _is_pubmed_xml(record):
            return _parse_pubmed_xml(record)
        if _is_medline_text(record):
            return _parse_medline_text(record)
        logger.warning("Unrecognized PubMed record shape; keys=%s", list(record.keys())[:12])
        return None
    except Exception:
        logger.exception("Failed to parse PubMed record")
        return None


def parse_pubmed_records(
    records: Union[Iterable[Mapping[str, Any]], Mapping[str, Any], Sequence[Any], None],
) -> list[Article]:
    """Parse a batch of Entrez records, skipping failures.

    Accepts:
    * an iterable of Medline / PubmedArticle dicts
    * an Entrez ``read()`` payload containing ``PubmedArticle``
    * ``None`` (returns ``[]``)
    """
    if records is None:
        return []

    # Full Entrez XML payload: {"PubmedArticle": [...], "PubmedBookArticle": [...]}
    if isinstance(records, Mapping) and "PubmedArticle" in records:
        article_list = records.get("PubmedArticle") or []
        items: Iterable[Any] = article_list
    elif isinstance(records, Mapping) and (
        "MedlineCitation" in records or "PMID" in records or "TI" in records
    ):
        items = [records]
    else:
        items = records  # type: ignore[assignment]

    articles: list[Article] = []
    seen: set[str] = set()
    for raw in items:
        article = parse_pubmed_record(raw)
        if article is None:
            continue
        if article.source_id in seen:
            logger.debug("Dropping duplicate PMID in batch: %s", article.source_id)
            continue
        seen.add(article.source_id)
        articles.append(article)

    logger.info("Parsed %d article(s) from PubMed records", len(articles))
    return articles
