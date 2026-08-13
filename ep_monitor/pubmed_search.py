"""Build PubMed queries and fetch PMIDs / records via NCBI Entrez.

Implements ``IntelligenceSource`` so the pipeline can later swap in
ClinicalTrials.gov or other sources without changing ``main.py``.

Query shape
-----------
(ablation technologies OR EP topics)
AND (cardiac diseases)
AND (competitor companies OR product keywords)
[+ optional PDAT date window]
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from ep_monitor import config
from ep_monitor.company_matcher import companies_for_query
from ep_monitor.models.article import Article
from ep_monitor.pubmed_parser import parse_pubmed_records
from ep_monitor.sources.base import IntelligenceSource

logger = logging.getLogger(__name__)

# NCBI recommends batching; keep under typical efetch limits.
_ESEARCH_PAGE_SIZE = 500
_EFETCH_BATCH_SIZE = 200
_REQUEST_PAUSE_SEC = 0.34  # ~3 req/s without API key; still fine with a key


def _quote_term(term: str) -> str:
    """Wrap a search term in double quotes for PubMed phrase search."""
    cleaned = term.strip()
    if not cleaned:
        return '""'
    # Escape embedded quotes (rare in our vocabularies)
    cleaned = cleaned.replace('"', "")
    return f'"{cleaned}"'


def _or_clause(terms: Sequence[str]) -> str:
    """Build ``("a" OR "b" OR …)``; empty input raises ValueError."""
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(term.strip())
    if not unique:
        raise ValueError("OR clause requires at least one term")
    return "(" + " OR ".join(_quote_term(t) for t in unique) + ")"


def _format_pubmed_date(value: date) -> str:
    """Format a date as ``YYYY/MM/DD`` for PubMed PDAT filters."""
    return value.strftime("%Y/%m/%d")


def configure_entrez() -> bool:
    """Configure ``Bio.Entrez`` email / API key from environment config.

    Returns:
        True if a usable email is configured; False otherwise.
    """
    try:
        from Bio import Entrez
    except ImportError:
        logger.error("biopython is not installed; run: pip install -r requirements.txt")
        return False

    email = (config.NCBI_EMAIL or "").strip()
    if not email:
        logger.error(
            "NCBI_EMAIL is not set. NCBI requires a contact email for Entrez. "
            "Copy .env.example to .env and set NCBI_EMAIL."
        )
        return False

    Entrez.email = email
    api_key = (config.NCBI_API_KEY or "").strip()
    if api_key:
        Entrez.api_key = api_key
        logger.debug("Entrez configured with API key for %s", email)
    else:
        Entrez.api_key = None
        logger.warning(
            "NCBI_API_KEY not set; using unauthenticated Entrez rate limits (~3 req/s)."
        )
    return True


def build_pubmed_query(
    lookback_days: int = 7,
    end_date: date | None = None,
    *,
    start_date: date | None = None,
    include_date_filter: bool = True,
    technology_terms: Sequence[str] | None = None,
    disease_terms: Sequence[str] | None = None,
    ep_terms: Sequence[str] | None = None,
    company_terms: Sequence[str] | None = None,
    playbook: dict | None = None,
) -> str:
    """Construct an optimized Boolean PubMed query.

    Combines ablation technologies, disease terms, EP topics, competitor
    company names, AND product keywords from the playbook (or config
    fallback). Optionally restricts to a publication-date window via ``[PDAT]``.
    """
    from ep_monitor import playbook as pb

    end = end_date or date.today()
    if start_date is None:
        days = max(int(lookback_days), 1)
        start = end - timedelta(days=days - 1)
    else:
        start = start_date

    book = playbook if playbook is not None else pb.load_playbook()
    domains = pb.enabled_domains(book)

    tech: list[str] = []
    diseases: list[str] = []
    ep: list[str] = []
    if technology_terms is not None:
        tech = list(technology_terms)
    if disease_terms is not None:
        diseases = list(disease_terms)
    if ep_terms is not None:
        ep = list(ep_terms)

    if technology_terms is None or disease_terms is None or ep_terms is None:
        if domains:
            for domain in domains:
                vocab = pb.query_vocab_for_domain(domain)
                if technology_terms is None:
                    tech.extend(vocab["technologies"])
                if disease_terms is None:
                    diseases.extend(vocab["diseases"])
                if ep_terms is None:
                    ep.extend(vocab["ep_topics"])
        else:
            if technology_terms is None:
                tech = list(config.ABLATION_TECHNOLOGIES)
            if disease_terms is None:
                diseases = list(config.CARDIAC_DISEASES)
            if ep_terms is None:
                ep = list(config.EP_TOPICS)

    # Dedupe while preserving order
    def _dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            key = item.casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item.strip())
        return out

    tech = _dedupe(tech)
    diseases = _dedupe(diseases)
    ep = _dedupe(ep)

    if company_terms is not None:
        companies = list(company_terms)
    else:
        # Include J&J portfolio names/products so own evidence is retrieved too.
        cmap = pb.company_product_map(book, include_own=True)
        companies = companies_for_query(company_map=cmap, include_own_portfolio=True)

    # (ablation ∪ EP) ∧ diseases ∧ (companies ∪ products)
    topic_clause = _or_clause(tech + ep)
    disease_clause = _or_clause(diseases)
    company_clause = _or_clause(companies)

    parts = [topic_clause, disease_clause, company_clause]
    query = " AND ".join(parts)

    if include_date_filter:
        if start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        date_clause = (
            f'("{_format_pubmed_date(start)}"[PDAT] : '
            f'"{_format_pubmed_date(end)}"[PDAT])'
        )
        query = f"({query}) AND {date_clause}"

    logger.debug("Built PubMed query (%d chars): %s", len(query), query)
    return query


def _esearch_pmids(query: str, *, retmax: int = _ESEARCH_PAGE_SIZE) -> list[str]:
    """Run Entrez esearch and return all matching PMIDs (paginated)."""
    from Bio import Entrez

    pmids: list[str] = []
    retstart = 0
    total: Optional[int] = None

    while True:
        logger.info("Entrez esearch retstart=%d retmax=%d", retstart, retmax)
        handle = Entrez.esearch(
            db="pubmed",
            term=query,
            retstart=retstart,
            retmax=retmax,
            sort="pub+date",
            retmode="xml",
        )
        try:
            result = Entrez.read(handle)
        finally:
            handle.close()

        if total is None:
            total = int(result.get("Count", "0"))
            logger.info("PubMed esearch hit count: %d", total)
            if total == 0:
                return []

        id_list = [str(x) for x in (result.get("IdList") or [])]
        pmids.extend(id_list)

        retstart += len(id_list)
        if not id_list or retstart >= total:
            break
        time.sleep(_REQUEST_PAUSE_SEC)

    # Preserve order while deduping
    seen: set[str] = set()
    unique: list[str] = []
    for pmid in pmids:
        if pmid not in seen:
            seen.add(pmid)
            unique.append(pmid)
    return unique


def _chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _efetch_articles(pmids: Sequence[str]) -> list[Article]:
    """Fetch and parse Medline records for the given PMIDs."""
    from Bio import Entrez
    from Bio import Medline

    if not pmids:
        return []

    articles: list[Article] = []
    for batch in _chunked(list(pmids), _EFETCH_BATCH_SIZE):
        logger.info("Entrez efetch batch of %d PMID(s)", len(batch))
        handle = Entrez.efetch(
            db="pubmed",
            id=",".join(batch),
            rettype="medline",
            retmode="text",
        )
        try:
            records = list(Medline.parse(handle))
        finally:
            handle.close()

        articles.extend(parse_pubmed_records(records))
        if len(batch) == _EFETCH_BATCH_SIZE:
            time.sleep(_REQUEST_PAUSE_SEC)

    return articles


class PubMedSource(IntelligenceSource):
    """PubMed / Entrez competitive-intelligence source."""

    def __init__(self, *, include_date_in_query: bool = True) -> None:
        """Create a PubMed source.

        Args:
            include_date_in_query: If True, embed PDAT in the Boolean query.
                ``fetch`` always also scopes by the provided date range.
        """
        self.include_date_in_query = include_date_in_query

    @property
    def name(self) -> str:
        return "pubmed"

    def fetch(self, start_date: date, end_date: date) -> list[Article]:
        """Search PubMed and return normalized ``Article`` objects.

        Args:
            start_date: Inclusive start of publication window.
            end_date: Inclusive end of publication window.

        Returns:
            List of articles (may be empty). Soft API failures are logged
            and yield ``[]`` rather than raising.
        """
        if start_date > end_date:
            logger.error("Invalid date range: %s > %s", start_date, end_date)
            return []

        if not configure_entrez():
            return []

        query = build_pubmed_query(
            start_date=start_date,
            end_date=end_date,
            include_date_filter=self.include_date_in_query,
        )
        logger.info(
            "Searching PubMed from %s to %s",
            start_date.isoformat(),
            end_date.isoformat(),
        )
        logger.info("Query: %s", query)

        try:
            pmids = _esearch_pmids(query)
        except Exception:
            logger.exception("PubMed esearch failed")
            return []

        if not pmids:
            logger.info("No PubMed hits for this window")
            return []

        try:
            articles = _efetch_articles(pmids)
        except Exception:
            logger.exception("PubMed efetch failed")
            return []

        logger.info("Retrieved %d PubMed article(s)", len(articles))
        return articles


def search_pubmed(
    lookback_days: int = 7,
    end_date: date | None = None,
) -> list[Article]:
    """Convenience wrapper used by ``main.py`` for the default PubMed run.

    Args:
        lookback_days: Inclusive day window ending at ``end_date``.
        end_date: Window end (default: today).

    Returns:
        Newly fetched articles (not yet deduplicated against the database).
    """
    end = end_date or date.today()
    days = max(int(lookback_days), 1)
    start = end - timedelta(days=days - 1)
    return PubMedSource().fetch(start, end)
