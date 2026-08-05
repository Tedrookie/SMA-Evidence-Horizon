"""Attribute articles to competitor companies via name + product keywords.

Many high-value papers never mention "Boston Scientific" — they say
FARAPULSE / Farawave instead. This module scans title + abstract and
fills ``Article.matched_companies`` / ``matched_products``.

Matching rules
--------------
* Case-insensitive, word-boundary aware (avoids ``RF`` matching inside ``PERF``).
* Company display names are matched in addition to product keywords.
* Longer product phrases win over shorter substrings (e.g. ``EnSite X``
  suppresses a redundant ``EnSite`` hit; ``Volt PFA`` suppresses ``Volt``).
* Duplicate case variants in ``COMPANY_PRODUCT_MAP`` are collapsed.
"""

from __future__ import annotations

import logging
import re

from ep_monitor.config import COMPANY_PRODUCT_MAP, COMPETITOR_COMPANIES
from ep_monitor.models.article import Article

logger = logging.getLogger(__name__)

_OWN_PORTFOLIO: frozenset[str] = frozenset({"Johnson & Johnson"})
_COMPETITOR_SET: frozenset[str] = frozenset(COMPETITOR_COMPANIES)


def _contains_phrase(text: str, phrase: str) -> bool:
    """Return True if ``phrase`` appears as a whole token/phrase in ``text``."""
    if not phrase or not text:
        return False
    pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(text))


def _article_search_text(article: Article) -> str:
    """Concatenate fields that may contain company or product names."""
    parts = [
        article.title or "",
        article.abstract or "",
        article.journal or "",
        " ".join(article.authors or []),
    ]
    affiliations = article.raw_metadata.get("affiliations")
    if isinstance(affiliations, list):
        parts.append(" ".join(str(a) for a in affiliations))
    elif isinstance(affiliations, str):
        parts.append(affiliations)
    return "\n".join(parts)


def _unique_keywords(keywords: list[str]) -> list[str]:
    """Collapse case-variant duplicates; prefer the longest spelling."""
    best: dict[str, str] = {}
    for kw in keywords:
        key = kw.casefold().strip()
        if not key:
            continue
        existing = best.get(key)
        if existing is None or len(kw) > len(existing):
            best[key] = kw.strip()
    return sorted(best.values(), key=len, reverse=True)


def _dedupe_prefer_longer(phrases: list[str]) -> list[str]:
    """Drop shorter phrases that are substrings of an already kept phrase."""
    ordered = sorted({p.strip() for p in phrases if p.strip()}, key=len, reverse=True)
    kept: list[str] = []
    for phrase in ordered:
        lower = phrase.casefold()
        if any(lower in k.casefold() for k in kept):
            continue
        kept.append(phrase)
    return kept


def _iter_companies(
    company_map: dict[str, list[str]],
    competitors_only: bool,
    *,
    using_default_map: bool,
) -> list[tuple[str, list[str]]]:
    """Return (company, products) pairs respecting the competitor filter."""
    items: list[tuple[str, list[str]]] = []
    for company, products in company_map.items():
        if competitors_only:
            if using_default_map:
                if company not in _COMPETITOR_SET:
                    continue
            elif company in _OWN_PORTFOLIO:
                continue
        items.append((company, products))
    return items


def match_companies(
    article: Article,
    company_map: dict[str, list[str]] | None = None,
    competitors_only: bool = True,
) -> Article:
    """Annotate ``article`` in-place with matched companies and products.

    Args:
        article: Normalized article.
        company_map: Override for tests; defaults to ``COMPANY_PRODUCT_MAP``.
        competitors_only: If True, only attribute to ``COMPETITOR_COMPANIES``
            (excludes Johnson & Johnson / Biosense Webster portfolio).

    Returns:
        The same article instance with match fields populated.
    """
    using_default_map = company_map is None
    mapping = COMPANY_PRODUCT_MAP if using_default_map else company_map
    text = _article_search_text(article)

    matched_companies: list[str] = []
    matched_products: list[str] = []

    for company, products in _iter_companies(
        mapping,
        competitors_only,
        using_default_map=using_default_map,
    ):
        company_hit = _contains_phrase(text, company)
        product_hits = _dedupe_prefer_longer(
            [kw for kw in _unique_keywords(products) if _contains_phrase(text, kw)]
        )

        if company_hit or product_hits:
            matched_companies.append(company)
            matched_products.extend(product_hits)
            logger.debug(
                "Matched %s → %s (company_name=%s, products=%s)",
                article.source_id,
                company,
                company_hit,
                product_hits,
            )

    article.matched_companies = matched_companies
    article.matched_products = _dedupe_prefer_longer(matched_products)

    if matched_companies:
        logger.info(
            "Article %s attributed to %s via products=%s",
            article.source_id,
            matched_companies,
            article.matched_products,
        )
    else:
        logger.debug("Article %s: no company/product match", article.source_id)

    return article


def match_articles(
    articles: list[Article],
    company_map: dict[str, list[str]] | None = None,
    competitors_only: bool = True,
) -> list[Article]:
    """Run ``match_companies`` on a batch and return the same list."""
    for article in articles:
        match_companies(
            article,
            company_map=company_map,
            competitors_only=competitors_only,
        )
    return articles


def primary_company(article: Article, default: str = "Unknown") -> str:
    """Return the first matched company, or ``default`` if none."""
    if article.matched_companies:
        return article.matched_companies[0]
    return default


def companies_for_query(
    company_map: dict[str, list[str]] | None = None,
    include_own_portfolio: bool = False,
) -> list[str]:
    """Return company + product terms for PubMed Boolean OR expansion.

    Args:
        company_map: Override map; defaults to ``COMPANY_PRODUCT_MAP``.
        include_own_portfolio: If True, also include J&J / Biosense Webster
            terms (useful for contrast searches; off by default).

    Returns:
        Deduplicated list of search terms (company names + product keywords)
        suitable for wrapping in quoted PubMed OR clauses.
    """
    using_default_map = company_map is None
    mapping = COMPANY_PRODUCT_MAP if using_default_map else company_map
    terms: list[str] = []

    for company, products in mapping.items():
        if using_default_map:
            if company in _COMPETITOR_SET:
                pass
            elif include_own_portfolio and company in _OWN_PORTFOLIO:
                pass
            else:
                continue
        elif company in _OWN_PORTFOLIO and not include_own_portfolio:
            continue

        terms.append(company)
        terms.extend(_unique_keywords(products))

    return _unique_keywords(terms)
