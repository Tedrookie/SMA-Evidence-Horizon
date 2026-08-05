"""Summarize and score articles via the OpenAI API.

Produces a structured competitive-intelligence brief aligned with the
Biosense Webster monitoring workflow: technology / disease / company
labels, narrative summary, key findings, clinical impact, competitive
implications, and an importance score (1–10).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Optional

from ep_monitor import config
from ep_monitor.company_matcher import primary_company
from ep_monitor.models.article import Article, ArticleSummary

logger = logging.getLogger(__name__)

_SUMMARY_JSON_SCHEMA_HINT = """
Return ONLY a single JSON object (no markdown fences) with these keys:
{
  "technology": one of ["PFA", "RF", "Cryo", "Mapping", "AI", "Other"],
  "disease": one of ["AF", "Persistent AF", "VT", "SVT", "Other"],
  "company": string (competitor name if identifiable, else "Unknown"),
  "study_type": string (e.g. "Randomized Trial", "Registry", "Cohort",
                "Animal Study", "Review", "Case Report"),
  "summary": string (150-250 words, concise clinical/CI summary),
  "key_findings": array of 3-5 short bullet strings,
  "clinical_impact": string (why clinicians should care),
  "competitive_intelligence": string (impact on competitive landscape
      vs Biosense Webster / J&J EP portfolio),
  "importance_score": integer from 1 to 10
}
""".strip()


def _build_user_prompt(article: Article) -> str:
    """Compose the user message with article content and matcher hints."""
    pub_date = (
        article.publication_date.isoformat()
        if isinstance(article.publication_date, date)
        else "Unknown"
    )
    matched_company = primary_company(article, default="Unknown")
    matched_products = ", ".join(article.matched_products) or "None detected"

    return f"""Analyze this cardiac electrophysiology publication for competitive intelligence.

### Basic Information (use these values; do not invent a different title/PMID)
- Title: {article.title}
- Journal: {article.journal or "Unknown"}
- Publication Date: {pub_date}
- PMID: {article.source_id}
- URL: {article.url or "N/A"}
- Authors: {", ".join(article.authors) if article.authors else "Unknown"}

### Pre-matched competitor signals (from product/company keyword scan)
- Matched company hint: {matched_company}
- Matched products: {matched_products}
Prefer the hint when consistent with the abstract; override only with clear evidence.

### Abstract
{article.abstract or "(No abstract available — infer cautiously from the title only.)"}

{_SUMMARY_JSON_SCHEMA_HINT}

Scoring guide for importance_score:
- 9-10: Practice-changing RCT / pivotal trial / major competitive threat to Biosense Webster
- 7-8: High-impact clinical data, new indication, or clear competitive positioning
- 4-6: Useful but incremental; niche population or limited generalizability
- 1-3: Low relevance, opinion-only, or weak methods
""".strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: first {...} block
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError("Model response JSON must be an object")
    return data


def _normalize_label(value: Any, allowed: list[str], default: str = "Other") -> str:
    """Map a free-text label onto an allowed vocabulary (case-insensitive)."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    for label in allowed:
        if text.casefold() == label.casefold():
            return label
    # Common aliases
    aliases = {
        "pulsed field": "PFA",
        "pulsed-field": "PFA",
        "radiofrequency": "RF",
        "radio-frequency": "RF",
        "cryoballoon": "Cryo",
        "cryoablation": "Cryo",
        "atrial fibrillation": "AF",
        "persistent atrial fibrillation": "Persistent AF",
        "ventricular tachycardia": "VT",
        "supraventricular tachycardia": "SVT",
    }
    folded = text.casefold()
    for needle, mapped in aliases.items():
        if needle in folded and mapped in allowed:
            return mapped
    logger.debug("Unrecognized label %r; defaulting to %s", text, default)
    return default


def _clamp_score(value: Any) -> int:
    """Coerce importance score into integer 1–10."""
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        logger.warning("Invalid importance_score %r; defaulting to 5", value)
        return 5
    return max(1, min(10, score))


def _ensure_findings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    findings = [str(item).strip() for item in value if str(item).strip()]
    return findings[:5]


def parse_summary_payload(
    payload: dict[str, Any],
    article: Article,
) -> ArticleSummary:
    """Validate / normalize a model JSON payload into ``ArticleSummary``.

    Exposed for unit tests without calling the OpenAI API.
    """
    company = str(payload.get("company") or "").strip() or primary_company(article)
    if company.casefold() in {"n/a", "none", "null"}:
        company = "Unknown"

    summary_text = str(payload.get("summary") or "").strip()
    if not summary_text:
        raise ValueError("summary field is empty")

    return ArticleSummary(
        source_id=article.source_id,
        title=article.title,
        journal=article.journal,
        publication_date=article.publication_date,
        technology=_normalize_label(
            payload.get("technology"),
            config.TECHNOLOGY_LABELS,
            default="Other",
        ),
        disease=_normalize_label(
            payload.get("disease"),
            config.DISEASE_LABELS,
            default="Other",
        ),
        company=company,
        study_type=str(payload.get("study_type") or "Other").strip() or "Other",
        summary=summary_text,
        key_findings=_ensure_findings(payload.get("key_findings")),
        clinical_impact=str(payload.get("clinical_impact") or "").strip()
        or "Not specified.",
        competitive_intelligence=str(
            payload.get("competitive_intelligence") or ""
        ).strip()
        or "Not specified.",
        importance_score=_clamp_score(payload.get("importance_score")),
        url=article.url,
        matched_products=list(article.matched_products),
    )


def _get_openai_client() -> Any:
    """Create an OpenAI-compatible client (OpenAI, DeepSeek, etc.)."""
    api_key = (config.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError(
            "No LLM API key set. Add OPENAI_API_KEY or DEEPSEEK_API_KEY to .env "
            "(see .env.example)."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package is not installed; run: pip install -r requirements.txt"
        ) from exc

    base_url = (config.OPENAI_BASE_URL or "").strip() or None
    if base_url:
        logger.info("Using OpenAI-compatible endpoint: %s", base_url)
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _call_openai(article: Article) -> dict[str, Any]:
    """Execute one chat completion and return the parsed JSON object."""
    client = _get_openai_client()
    model = config.OPENAI_MODEL or "gpt-4o-mini"

    logger.info(
        "Summarizing PMID %s with model=%s",
        article.source_id,
        model,
    )
    # json_object response_format is supported by OpenAI and DeepSeek chat models.
    create_kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": config.OPENAI_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(article)},
        ],
    }
    # Prefer structured JSON when the provider supports it.
    try:
        response = client.chat.completions.create(
            response_format={"type": "json_object"},
            **create_kwargs,
        )
    except Exception as exc:
        # Some compatible endpoints reject response_format; fall back.
        if "response_format" in str(exc).lower() or "json_object" in str(exc).lower():
            logger.warning(
                "Provider rejected response_format=json_object; retrying without it (%s)",
                exc,
            )
            response = client.chat.completions.create(**create_kwargs)
        else:
            raise

    content = response.choices[0].message.content or ""
    if not content.strip():
        raise ValueError("Empty completion content from LLM")
    return _extract_json_object(content)

def summarize_article(article: Article) -> ArticleSummary | None:
    """Call OpenAI to produce a structured competitive-intelligence summary.

    Args:
        article: New, unprocessed article (preferably after company matching).

    Returns:
        ``ArticleSummary`` on success, or ``None`` if the API call / parse
        fails (logged; pipeline continues with remaining articles).
    """
    try:
        payload = _call_openai(article)
        summary = parse_summary_payload(payload, article)
    except Exception:
        logger.exception(
            "Failed to summarize article %s (%s)",
            article.source_id,
            article.title[:80],
        )
        return None

    logger.info(
        "Summarized %s → tech=%s disease=%s company=%s score=%d",
        article.source_id,
        summary.technology,
        summary.disease,
        summary.company,
        summary.importance_score,
    )
    return summary


def summarize_articles(articles: list[Article]) -> list[ArticleSummary]:
    """Summarize a batch; skip individual failures."""
    results: list[ArticleSummary] = []
    for index, article in enumerate(articles, start=1):
        logger.info(
            "Summarizing %d/%d: %s",
            index,
            len(articles),
            article.source_id,
        )
        summary = summarize_article(article)
        if summary is not None:
            results.append(summary)
    logger.info(
        "Summarization complete: %d success / %d failed (of %d)",
        len(results),
        len(articles) - len(results),
        len(articles),
    )
    return results
