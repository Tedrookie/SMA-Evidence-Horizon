"""Load / save the centralized surveillance playbook (YAML).

The playbook holds domains, keywords, companies, products, schedule, and
recipients. Secrets remain in ``.env``.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from ep_monitor import config

logger = logging.getLogger(__name__)

PLAYBOOK_PATH = config.DATA_DIR / "playbook.yaml"


def default_playbook() -> dict[str, Any]:
    """Build a playbook dict from legacy ``config.py`` values (fallback)."""
    companies = []
    for name in config.COMPETITOR_COMPANIES:
        companies.append(
            {
                "name": name,
                "products": list(config.COMPANY_PRODUCT_MAP.get(name, [])),
            }
        )
    own = []
    for name, products in config.COMPANY_PRODUCT_MAP.items():
        if name not in config.COMPETITOR_COMPANIES:
            own.append({"name": name, "products": list(products)})

    return {
        "meta": {
            "product_name": "Evidence Horizon",
            "owner": "Strategic Medical Affairs / JJMC",
            "tagline": "Turning medical evidence into strategic clarity.",
        },
        "schedule": {
            "mode": config.SCHEDULE_MODE,
            "weekday": "monday",
            "hour": 8,
            "minute": 0,
            "lookback_days": config.LOOKBACK_DAYS,
        },
        "recipients": [
            {"name": "", "email": addr} for addr in config.EMAIL_TO
        ],
        "domains": [
            {
                "id": "ep",
                "name": "Cardiac Electrophysiology",
                "enabled": True,
                "technologies": list(config.ABLATION_TECHNOLOGIES),
                "diseases": list(config.CARDIAC_DISEASES),
                "ep_topics": list(config.EP_TOPICS),
                "companies": companies,
                "own_portfolio": own,
            }
        ],
    }


def load_playbook(path: Path | None = None) -> dict[str, Any]:
    """Load playbook YAML; fall back to ``default_playbook()`` if missing."""
    pb_path = path or PLAYBOOK_PATH
    if not pb_path.exists():
        logger.warning("Playbook not found at %s; using config.py defaults", pb_path)
        return default_playbook()

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required for the playbook. Run: pip install -r requirements.txt"
        ) from exc

    with pb_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Playbook must be a mapping: {pb_path}")
    merged = default_playbook()
    merged.update({k: v for k, v in data.items() if v is not None})
    # Deep-merge meta/schedule lightly
    if isinstance(data.get("meta"), dict):
        merged["meta"] = {**default_playbook()["meta"], **data["meta"]}
    if isinstance(data.get("schedule"), dict):
        merged["schedule"] = {**default_playbook()["schedule"], **data["schedule"]}
    if "domains" in data and isinstance(data["domains"], list):
        merged["domains"] = data["domains"]
    if "recipients" in data and isinstance(data["recipients"], list):
        merged["recipients"] = data["recipients"]
    return merged


def save_playbook(data: dict[str, Any], path: Path | None = None) -> Path:
    """Write playbook YAML to disk."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required for the playbook. Run: pip install -r requirements.txt"
        ) from exc

    pb_path = path or PLAYBOOK_PATH
    pb_path.parent.mkdir(parents=True, exist_ok=True)
    payload = copy.deepcopy(data)
    with pb_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            payload,
            fh,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    logger.info("Playbook saved to %s", pb_path)
    return pb_path


def enabled_domains(playbook: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return domains with ``enabled: true``."""
    pb = playbook if playbook is not None else load_playbook()
    domains = pb.get("domains") or []
    return [d for d in domains if isinstance(d, dict) and d.get("enabled", True)]


def lookback_days(playbook: dict[str, Any] | None = None) -> int:
    pb = playbook if playbook is not None else load_playbook()
    sched = pb.get("schedule") or {}
    try:
        return max(int(sched.get("lookback_days", config.LOOKBACK_DAYS)), 1)
    except (TypeError, ValueError):
        return config.LOOKBACK_DAYS


def recipient_emails(playbook: dict[str, Any] | None = None) -> list[str]:
    """Emails from playbook recipients; fall back to ``EMAIL_TO``."""
    pb = playbook if playbook is not None else load_playbook()
    emails: list[str] = []
    for row in pb.get("recipients") or []:
        if isinstance(row, dict):
            addr = (row.get("email") or "").strip()
        else:
            addr = str(row).strip()
        if addr and addr not in emails:
            emails.append(addr)
    return emails or list(config.EMAIL_TO)


def company_product_map(
    playbook: dict[str, Any] | None = None,
    *,
    include_own: bool = True,
    domain_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    """Flatten enabled-domain companies (+ optional own portfolio) into a map."""
    domains = enabled_domains(playbook)
    if domain_ids is not None:
        wanted = set(domain_ids)
        domains = [d for d in domains if d.get("id") in wanted]

    mapping: dict[str, list[str]] = {}
    for domain in domains:
        for row in domain.get("companies") or []:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            products = [str(p).strip() for p in (row.get("products") or []) if str(p).strip()]
            mapping.setdefault(name, [])
            for p in products:
                if p not in mapping[name]:
                    mapping[name].append(p)
        if include_own:
            for row in domain.get("own_portfolio") or []:
                if not isinstance(row, dict):
                    continue
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                products = [str(p).strip() for p in (row.get("products") or []) if str(p).strip()]
                mapping.setdefault(name, [])
                for p in products:
                    if p not in mapping[name]:
                        mapping[name].append(p)
    return mapping


def competitor_names(playbook: dict[str, Any] | None = None) -> list[str]:
    """Competitor company display names from enabled domains (excludes own portfolio)."""
    domains = enabled_domains(playbook)
    names: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        for row in domain.get("companies") or []:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "").strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def query_vocab_for_domain(domain: dict[str, Any]) -> dict[str, list[str]]:
    """Extract PubMed query building blocks from one domain block."""
    return {
        "technologies": [str(x).strip() for x in (domain.get("technologies") or []) if str(x).strip()],
        "diseases": [str(x).strip() for x in (domain.get("diseases") or []) if str(x).strip()],
        "ep_topics": [str(x).strip() for x in (domain.get("ep_topics") or []) if str(x).strip()],
    }


def product_name(playbook: dict[str, Any] | None = None) -> str:
    pb = playbook if playbook is not None else load_playbook()
    meta = pb.get("meta") or {}
    return str(meta.get("product_name") or "Evidence Horizon")


def tagline(playbook: dict[str, Any] | None = None) -> str:
    pb = playbook if playbook is not None else load_playbook()
    meta = pb.get("meta") or {}
    return str(meta.get("tagline") or "Moving healthcare forward, together.")
