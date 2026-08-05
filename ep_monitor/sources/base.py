"""Abstract base for all competitive-intelligence data sources.

Future sources (ClinicalTrials.gov, FDA, HRS abstracts, patents,
Semantic Scholar, RSS) should implement this interface so the
pipeline in ``main.py`` stays unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ep_monitor.models.article import Article


class IntelligenceSource(ABC):
    """Contract every data source must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and the database (e.g. 'pubmed')."""

    @abstractmethod
    def fetch(
        self,
        start_date: date,
        end_date: date,
    ) -> list[Article]:
        """Retrieve new items published between ``start_date`` and ``end_date``."""
