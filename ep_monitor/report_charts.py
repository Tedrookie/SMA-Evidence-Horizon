"""Chart generation for EP PubMed Digest HTML/email reports.

Produces PNG charts (company bar + company x technology bubble matrix)
embedded as base64 data URIs. Technology tags are inferred from
title/abstract keywords so the basic (no-LLM) pipeline can use them.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from collections import Counter
from typing import Sequence

from ep_monitor.config import COMPETITOR_COMPANIES
from ep_monitor.models.article import Article

logger = logging.getLogger(__name__)

_JJ_RED = "#c8102e"
_TEXT = "#111827"
_MUTED = "#6b7280"
_GRID = "#e5e7eb"
_BG = "#ffffff"

# Display order for tech axis (AI folded into Other for a cleaner matrix)
TECH_AXIS: list[str] = ["PFA", "RF", "Cryo", "Mapping", "Other"]

_TECH_RULES: list[tuple[str, list[str]]] = [
    (
        "PFA",
        [
            r"\bpulsed[\s\-]?field\b",
            r"\bpfa\b",
            r"\bfarapulse\b",
            r"\bfarawave\b",
            r"\bpulseselect\b",
            r"\bvaripulse\b",
            r"\bvolt\s*pfa\b",
            r"\baffera\b",
            r"\bsphere[\s\-]?9\b",
        ],
    ),
    (
        "RF",
        [
            r"\bradiofrequency\b",
            r"\brf\s+ablation\b",
            r"\brfa\b",
            r"\bthermocool\b",
            r"\bqdot\b",
            r"\btactiflex\b",
            r"\btacticath\b",
            r"\bdiamondtemp\b",
            r"\bcontact[\s\-]?force\b",
        ],
    ),
    (
        "Cryo",
        [
            r"\bcryoballoon\b",
            r"\bcryoablation\b",
            r"\bcryo\b",
            r"\barctic\s+front\b",
            r"\bcryoice\b",
        ],
    ),
    (
        "Mapping",
        [
            r"\belectroanatomic\b",
            r"\b3d\s+mapping\b",
            r"\bmapping\s+system\b",
            r"\bhigh[\s\-]?density\s+mapping\b",
            r"\bensite\b",
            r"\bcarto\b",
            r"\badvisor\s+hd\b",
            r"\boctaray\b",
            r"\boptrell\b",
            r"\bcolumbus\b",
        ],
    ),
    (
        "AI",
        [
            r"\bartificial\s+intelligence\b",
            r"\bmachine\s+learning\b",
            r"\bdeep\s+learning\b",
            r"\bneural\s+network\b",
        ],
    ),
]


def infer_technologies(article: Article) -> list[str]:
    """Infer technology labels from title + abstract + matched products."""
    blob = " ".join(
        [
            article.title or "",
            article.abstract or "",
            " ".join(article.matched_products or []),
        ]
    ).casefold()

    hits: list[str] = []
    for label, patterns in _TECH_RULES:
        if any(re.search(p, blob) for p in patterns):
            # Map AI into Other for the matrix axis
            hits.append("Other" if label == "AI" else label)

    # Deduplicate preserving TECH_AXIS order
    ordered = [t for t in TECH_AXIS if t in hits]
    return ordered if ordered else ["Other"]


def _companies_for_article(article: Article) -> list[str]:
    if article.matched_companies:
        return list(article.matched_companies)
    return ["Unmatched"]


def company_counts(articles: Sequence[Article]) -> list[tuple[str, int]]:
    """Return (company, count) sorted by count desc, competitors first."""
    counter: Counter[str] = Counter()
    for article in articles:
        for company in _companies_for_article(article):
            counter[company] += 1

    preferred = list(COMPETITOR_COMPANIES) + ["Unmatched"]
    labels = [c for c in preferred if c in counter]
    labels += sorted(
        (c for c in counter if c not in labels),
        key=lambda x: (-counter[x], x.casefold()),
    )
    return [(c, counter[c]) for c in labels]


def company_tech_matrix(
    articles: Sequence[Article],
) -> tuple[list[str], list[str], dict[tuple[str, str], int]]:
    """Build company x technology counts.

    Returns:
        companies (row labels), techs (col labels), cell counts.
    """
    cells: Counter[tuple[str, str]] = Counter()
    company_set: set[str] = set()

    for article in articles:
        techs = infer_technologies(article)
        for company in _companies_for_article(article):
            company_set.add(company)
            for tech in techs:
                cells[(company, tech)] += 1

    preferred = list(COMPETITOR_COMPANIES) + ["Unmatched"]
    companies = [c for c in preferred if c in company_set]
    companies += sorted(
        (c for c in company_set if c not in companies),
        key=str.casefold,
    )
    techs = list(TECH_AXIS)
    return companies, techs, dict(cells)


def _png_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor=_BG)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buf.getvalue()


def _to_data_uri(png: bytes) -> str:
    encoded = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_company_bar_png(articles: Sequence[Article]) -> bytes | None:
    """Horizontal bar chart of papers per company."""
    if not articles:
        return None

    rows = company_counts(articles)
    if not rows:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping company bar chart")
        return None

    labels = [r[0] for r in rows][::-1]
    values = [r[1] for r in rows][::-1]

    fig_h = max(2.8, 0.45 * len(labels) + 1.2)
    fig, ax = plt.subplots(figsize=(6.4, fig_h))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    colors = [_JJ_RED if c != "Unmatched" else "#9ca3af" for c in labels]
    bars = ax.barh(labels, values, color=colors, height=0.62)
    ax.bar_label(bars, padding=4, fontsize=9, color=_TEXT, fontweight="bold")
    ax.set_xlabel("Papers", fontsize=10, color=_MUTED)
    ax.set_title("Papers by company", fontsize=12, fontweight="700", color=_TEXT, pad=10)
    ax.tick_params(colors=_TEXT, labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_GRID)
    ax.spines["bottom"].set_color(_GRID)
    ax.xaxis.grid(True, color=_GRID, linestyle="-", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlim(0, max(values) * 1.18 + 0.5)

    return _png_bytes(fig)


def render_bubble_matrix_png(articles: Sequence[Article]) -> bytes | None:
    """Company x technology bubble matrix; size = paper count."""
    if not articles:
        return None

    companies, techs, cells = company_tech_matrix(articles)
    if not companies:
        return None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping bubble matrix")
        return None

    max_count = max(cells.values()) if cells else 1
    xs: list[float] = []
    ys: list[float] = []
    sizes: list[float] = []
    annotations: list[tuple[float, float, int]] = []

    # Competitors at top (matplotlib y increases upward → reverse list)
    companies = list(reversed(companies))

    for yi, company in enumerate(companies):
        for xi, tech in enumerate(techs):
            count = cells.get((company, tech), 0)
            if count <= 0:
                continue
            # Keep bubbles inside one cell so neighbors do not overlap
            area = 80 + (count / max_count) * 280
            xs.append(float(xi))
            ys.append(float(yi))
            sizes.append(area)
            annotations.append((float(xi), float(yi), count))

    fig_w = 7.0
    fig_h = max(4.0, 0.85 * len(companies) + 1.6)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    # Light grid of empty cells
    for yi in range(len(companies)):
        for xi in range(len(techs)):
            ax.scatter(
                [xi],
                [yi],
                s=28,
                c="#f3f4f6",
                edgecolors="#e5e7eb",
                linewidths=0.8,
                zorder=1,
            )

    if xs:
        ax.scatter(
            xs,
            ys,
            s=sizes,
            c=_JJ_RED,
            alpha=0.82,
            edgecolors="#9f1239",
            linewidths=0.9,
            zorder=2,
        )
        for xi, yi, count in annotations:
            ax.text(
                xi,
                yi,
                str(count),
                ha="center",
                va="center",
                fontsize=9,
                fontweight="700",
                color="#ffffff",
                zorder=3,
            )

    ax.set_xticks(range(len(techs)))
    ax.set_xticklabels(techs, fontsize=10, color=_TEXT, fontweight="bold")
    ax.set_yticks(range(len(companies)))
    ax.set_yticklabels(companies, fontsize=9, color=_TEXT)
    ax.set_xlim(-0.7, len(techs) - 0.3)
    ax.set_ylim(-0.7, len(companies) - 0.3)
    ax.set_xlabel("Technology (inferred)", fontsize=10, color=_MUTED, labelpad=14)
    ax.set_title(
        "Company × technology matrix",
        fontsize=12,
        fontweight="700",
        color=_TEXT,
        pad=14,
    )
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout(pad=1.2)

    return _png_bytes(fig)


def charts_as_data_uris(articles: Sequence[Article]) -> dict[str, str]:
    """Build embeddable chart data URIs for the HTML report.

    Returns keys: ``bubble_matrix``, ``company_bar`` (only when rendered).
    """
    result: dict[str, str] = {}
    try:
        bubble = render_bubble_matrix_png(articles)
        if bubble:
            result["bubble_matrix"] = _to_data_uri(bubble)
        bar = render_company_bar_png(articles)
        if bar:
            result["company_bar"] = _to_data_uri(bar)
    except Exception:
        logger.exception("Failed to render report charts")
    return result
