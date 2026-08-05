"""Data models for retrieved articles and AI-generated summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Article:
    """A normalized publication retrieved from any intelligence source.

    Designed to be source-agnostic so PubMed, ClinicalTrials.gov,
    conference abstracts, patents, etc. can all map into this shape.
    """

    source_id: str  # e.g. PMID, NCT ID, patent number
    source: str  # e.g. "pubmed", "clinicaltrials", "fda"
    title: str
    abstract: str
    journal: Optional[str] = None
    publication_date: Optional[date] = None
    authors: list[str] = field(default_factory=list)
    url: Optional[str] = None
    raw_metadata: dict = field(default_factory=dict)

    # Competitor attribution (filled by product/company matcher)
    matched_companies: list[str] = field(default_factory=list)
    matched_products: list[str] = field(default_factory=list)


@dataclass
class ArticleSummary:
    """Structured competitive-intelligence summary for one article."""

    source_id: str
    title: str
    journal: Optional[str]
    publication_date: Optional[date]
    technology: str
    disease: str
    company: str
    study_type: str
    summary: str
    key_findings: list[str]
    clinical_impact: str
    competitive_intelligence: str
    importance_score: int
    url: Optional[str] = None
    matched_products: list[str] = field(default_factory=list)
