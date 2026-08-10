"""SQLite persistence for processed article IDs and summaries.

Prevents re-summarizing / re-emailing the same paper. Schema is keyed by
``(source, source_id)`` so future sources (trials, FDA, patents) share
the same store without collisions.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ep_monitor.models.article import Article, ArticleSummary

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS processed_articles (
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    title       TEXT,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    source                   TEXT NOT NULL,
    source_id                TEXT NOT NULL,
    title                    TEXT NOT NULL,
    journal                  TEXT,
    publication_date         TEXT,
    technology               TEXT NOT NULL,
    disease                  TEXT NOT NULL,
    company                  TEXT NOT NULL,
    study_type               TEXT NOT NULL,
    summary                  TEXT NOT NULL,
    key_findings_json        TEXT NOT NULL,
    clinical_impact          TEXT NOT NULL,
    competitive_intelligence TEXT NOT NULL,
    importance_score         INTEGER NOT NULL,
    url                      TEXT,
    matched_products_json    TEXT NOT NULL DEFAULT '[]',
    created_at               TEXT NOT NULL,
    PRIMARY KEY (source, source_id),
    FOREIGN KEY (source, source_id)
        REFERENCES processed_articles (source, source_id)
);

CREATE TABLE IF NOT EXISTS articles (
    source                   TEXT NOT NULL,
    source_id                TEXT NOT NULL,
    title                    TEXT NOT NULL,
    abstract                 TEXT,
    journal                  TEXT,
    publication_date         TEXT,
    authors_json             TEXT NOT NULL DEFAULT '[]',
    url                      TEXT,
    matched_companies_json   TEXT NOT NULL DEFAULT '[]',
    matched_products_json    TEXT NOT NULL DEFAULT '[]',
    domain_id                TEXT,
    fetched_at               TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_summaries_score
    ON summaries (importance_score DESC);

CREATE INDEX IF NOT EXISTS idx_summaries_company
    ON summaries (company);

CREATE INDEX IF NOT EXISTS idx_articles_fetched
    ON articles (fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_articles_domain
    ON articles (domain_id);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _date_to_iso(value: date | None) -> Optional[str]:
    return value.isoformat() if value else None


def _iso_to_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        logger.debug("Could not parse date from DB: %r", value)
        return None


class ArticleDatabase(AbstractContextManager["ArticleDatabase"]):
    """Thin SQLite wrapper around processed intelligence items."""

    def __init__(self, db_path: Path | str, *, initialize: bool = True) -> None:
        """Open (or create) the database at ``db_path``.

        Args:
            db_path: Filesystem path to the SQLite file.
            initialize: If True, create tables immediately.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if initialize:
            self.initialize()
        logger.debug("Opened article database at %s", self.db_path)

    def __enter__(self) -> ArticleDatabase:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def initialize(self) -> None:
        """Create tables if they do not exist."""
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        logger.info("Database schema ready at %s", self.db_path)

    def is_processed(self, source: str, source_id: str) -> bool:
        """Return True if this item was already summarized / marked processed."""
        row = self._conn.execute(
            """
            SELECT 1 FROM processed_articles
            WHERE source = ? AND source_id = ?
            LIMIT 1
            """,
            (source, source_id),
        ).fetchone()
        return row is not None

    def filter_new(self, articles: list[Article]) -> list[Article]:
        """Drop articles already present in the database.

        Preserves input order. Also de-duplicates within the batch by
        ``(source, source_id)``.
        """
        fresh: list[Article] = []
        seen: set[tuple[str, str]] = set()
        skipped = 0

        for article in articles:
            key = (article.source, article.source_id)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            if self.is_processed(article.source, article.source_id):
                skipped += 1
                continue
            fresh.append(article)

        logger.info(
            "Dedupe: %d new / %d skipped (of %d fetched)",
            len(fresh),
            skipped,
            len(articles),
        )
        return fresh

    def mark_processed(
        self,
        source: str,
        source_id: str,
        title: str | None = None,
    ) -> None:
        """Record that an item was handled (even without a full summary)."""
        self._conn.execute(
            """
            INSERT INTO processed_articles (source, source_id, processed_at, title)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                processed_at = excluded.processed_at,
                title = COALESCE(excluded.title, processed_articles.title)
            """,
            (source, source_id, _utc_now_iso(), title),
        )
        self._conn.commit()

    def save_summary(self, summary: ArticleSummary, source: str = "pubmed") -> None:
        """Persist a completed summary and mark the source_id as processed."""
        now = _utc_now_iso()
        self.mark_processed(source, summary.source_id, title=summary.title)

        self._conn.execute(
            """
            INSERT INTO summaries (
                source, source_id, title, journal, publication_date,
                technology, disease, company, study_type, summary,
                key_findings_json, clinical_impact, competitive_intelligence,
                importance_score, url, matched_products_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(source, source_id) DO UPDATE SET
                title = excluded.title,
                journal = excluded.journal,
                publication_date = excluded.publication_date,
                technology = excluded.technology,
                disease = excluded.disease,
                company = excluded.company,
                study_type = excluded.study_type,
                summary = excluded.summary,
                key_findings_json = excluded.key_findings_json,
                clinical_impact = excluded.clinical_impact,
                competitive_intelligence = excluded.competitive_intelligence,
                importance_score = excluded.importance_score,
                url = excluded.url,
                matched_products_json = excluded.matched_products_json,
                created_at = excluded.created_at
            """,
            (
                source,
                summary.source_id,
                summary.title,
                summary.journal,
                _date_to_iso(summary.publication_date),
                summary.technology,
                summary.disease,
                summary.company,
                summary.study_type,
                summary.summary,
                json.dumps(summary.key_findings, ensure_ascii=False),
                summary.clinical_impact,
                summary.competitive_intelligence,
                int(summary.importance_score),
                summary.url,
                json.dumps(summary.matched_products, ensure_ascii=False),
                now,
            ),
        )
        self._conn.commit()
        logger.debug(
            "Saved summary %s:%s (score=%s)",
            source,
            summary.source_id,
            summary.importance_score,
        )

    def get_summary(self, source: str, source_id: str) -> ArticleSummary | None:
        """Load one stored summary, or ``None`` if missing."""
        row = self._conn.execute(
            """
            SELECT * FROM summaries
            WHERE source = ? AND source_id = ?
            """,
            (source, source_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_summary(row)

    def iter_summaries(
        self,
        *,
        min_score: int | None = None,
        company: str | None = None,
    ) -> Iterator[ArticleSummary]:
        """Yield stored summaries, optionally filtered."""
        clauses: list[str] = []
        params: list[Any] = []
        if min_score is not None:
            clauses.append("importance_score >= ?")
            params.append(min_score)
        if company is not None:
            clauses.append("company = ?")
            params.append(company)

        sql = "SELECT * FROM summaries"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY importance_score DESC, created_at DESC"

        for row in self._conn.execute(sql, params):
            yield self._row_to_summary(row)

    def count_processed(self, source: str | None = None) -> int:
        """Return number of processed items (optionally for one source)."""
        if source is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM processed_articles"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM processed_articles WHERE source = ?",
                (source,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def upsert_article(
        self,
        article: Article,
        *,
        domain_id: str | None = None,
    ) -> None:
        """Insert or update a full PubMed article row for the library."""
        self._conn.execute(
            """
            INSERT INTO articles (
                source, source_id, title, abstract, journal, publication_date,
                authors_json, url, matched_companies_json, matched_products_json,
                domain_id, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                title = excluded.title,
                abstract = excluded.abstract,
                journal = excluded.journal,
                publication_date = excluded.publication_date,
                authors_json = excluded.authors_json,
                url = excluded.url,
                matched_companies_json = excluded.matched_companies_json,
                matched_products_json = excluded.matched_products_json,
                domain_id = COALESCE(excluded.domain_id, articles.domain_id),
                fetched_at = excluded.fetched_at
            """,
            (
                article.source,
                article.source_id,
                article.title,
                article.abstract,
                article.journal,
                _date_to_iso(article.publication_date),
                json.dumps(article.authors or [], ensure_ascii=False),
                article.url,
                json.dumps(article.matched_companies or [], ensure_ascii=False),
                json.dumps(article.matched_products or [], ensure_ascii=False),
                domain_id,
                _utc_now_iso(),
            ),
        )
        self._conn.commit()

    def upsert_articles(
        self,
        articles: list[Article],
        *,
        domain_id: str | None = None,
    ) -> int:
        """Upsert a batch of articles. Returns count written."""
        for article in articles:
            self.upsert_article(article, domain_id=domain_id)
        logger.info("Upserted %d article(s) into library", len(articles))
        return len(articles)

    def list_articles(
        self,
        *,
        limit: int = 500,
        domain_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent library articles as plain dicts (for Excel / UI)."""
        params: list[Any] = []
        sql = "SELECT * FROM articles"
        if domain_id:
            sql += " WHERE domain_id = ?"
            params.append(domain_id)
        sql += " ORDER BY fetched_at DESC, publication_date DESC LIMIT ?"
        params.append(int(limit))

        rows: list[dict[str, Any]] = []
        for row in self._conn.execute(sql, params):
            rows.append(
                {
                    "source": row["source"],
                    "source_id": row["source_id"],
                    "pmid": row["source_id"],
                    "title": row["title"],
                    "abstract": row["abstract"] or "",
                    "journal": row["journal"] or "",
                    "publication_date": row["publication_date"] or "",
                    "authors": ", ".join(json.loads(row["authors_json"] or "[]")),
                    "url": row["url"] or "",
                    "matched_companies": ", ".join(
                        json.loads(row["matched_companies_json"] or "[]")
                    ),
                    "matched_products": ", ".join(
                        json.loads(row["matched_products_json"] or "[]")
                    ),
                    "domain_id": row["domain_id"] or "",
                    "fetched_at": row["fetched_at"] or "",
                }
            )
        return rows

    def count_articles(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]
            logger.debug("Closed article database at %s", self.db_path)

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> ArticleSummary:
        key_findings = json.loads(row["key_findings_json"] or "[]")
        matched_products = json.loads(row["matched_products_json"] or "[]")
        if not isinstance(key_findings, list):
            key_findings = []
        if not isinstance(matched_products, list):
            matched_products = []
        return ArticleSummary(
            source_id=row["source_id"],
            title=row["title"],
            journal=row["journal"],
            publication_date=_iso_to_date(row["publication_date"]),
            technology=row["technology"],
            disease=row["disease"],
            company=row["company"],
            study_type=row["study_type"],
            summary=row["summary"],
            key_findings=[str(x) for x in key_findings],
            clinical_impact=row["clinical_impact"],
            competitive_intelligence=row["competitive_intelligence"],
            importance_score=int(row["importance_score"]),
            url=row["url"],
            matched_products=[str(x) for x in matched_products],
        )
