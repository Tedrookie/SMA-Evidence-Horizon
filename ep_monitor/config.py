"""Central configuration for the EP competitive intelligence monitor.

All tunable parameters live here so modules stay independent of
hard-coded values. Secrets come from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = PACKAGE_ROOT  # backward-compatible alias
DATA_DIR = PACKAGE_ROOT / "data"
REPORTS_DIR = PACKAGE_ROOT / "reports"
EXPORTS_DIR = PACKAGE_ROOT / "exports"
DATABASE_PATH = DATA_DIR / "processed_articles.db"
PLAYBOOK_PATH = DATA_DIR / "playbook.yaml"
ARTICLES_DB_PATH = DATA_DIR / "articles_library.db"


def _load_dotenv() -> None:
    """Load ``.env`` from the repo root and current working directory."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()  # allow CWD overrides


_load_dotenv()

# ---------------------------------------------------------------------------
# API / email secrets (loaded from environment)
# ---------------------------------------------------------------------------

NCBI_EMAIL: str = os.getenv("NCBI_EMAIL", "")
NCBI_API_KEY: Optional[str] = os.getenv("NCBI_API_KEY")  # optional but recommended

# LLM provider (OpenAI-compatible: OpenAI, DeepSeek, etc.)
# DeepSeek example:
#   OPENAI_API_KEY=<deepseek-key>
#   OPENAI_BASE_URL=https://api.deepseek.com
#   OPENAI_MODEL=deepseek-chat
OPENAI_API_KEY: str = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("DEEPSEEK_API_KEY")
    or os.getenv("LLM_API_KEY")
    or ""
)
OPENAI_BASE_URL: Optional[str] = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("LLM_BASE_URL")
    or None
)
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini"

# If a DeepSeek key is provided without an explicit base URL, default to DeepSeek.
if not OPENAI_BASE_URL and os.getenv("DEEPSEEK_API_KEY"):
    OPENAI_BASE_URL = "https://api.deepseek.com"
if os.getenv("DEEPSEEK_API_KEY") and not os.getenv("OPENAI_MODEL") and not os.getenv("LLM_MODEL"):
    OPENAI_MODEL = "deepseek-chat"

SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM: str = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO: list[str] = [
    addr.strip()
    for addr in os.getenv("EMAIL_TO", "").split(",")
    if addr.strip()
]

# ---------------------------------------------------------------------------
# Pipeline thresholds
# ---------------------------------------------------------------------------

LOOKBACK_DAYS: int = int(os.getenv("LOOKBACK_DAYS", "7"))
IMPORTANCE_THRESHOLD: int = int(os.getenv("IMPORTANCE_THRESHOLD", "7"))
SCHEDULE_MODE: str = os.getenv("SCHEDULE_MODE", "weekly")  # "daily" | "weekly"

# ---------------------------------------------------------------------------
# Competitor companies + product-name mapping
# ---------------------------------------------------------------------------
# Papers often name a device (FARAPULSE, PulseSelect) without the parent
# company. Matching product keywords dramatically improves attribution.
# ---------------------------------------------------------------------------

COMPANY_PRODUCT_MAP: dict[str, list[str]] = {
    "Boston Scientific": [
        "FARAPULSE",
        "Farapulse",
        "Farawave",
        "Farastar",
        "FARAWAVE",
        "FARASTAR",
        "VersaCross",  # optional EP-adjacent
    ],
    "Medtronic": [
        "PulseSelect",
        "Affera",
        "Sphere-9",
        "Sphere 9",
        "Arctic Front",
        "DiamondTemp",
        "PVAC",
    ],
    "Abbott": [
        "EnSite X",
        "EnSite",
        "Volt PFA",
        "Volt",
        "TactiFlex",
        "TactiCath",
        "Advisor HD Grid",
        "Ampere",
    ],
    "MicroPort": [
        "FireMagic",
        "Columbus",
        "EasyFinder",
    ],
    "AtriCure": [
        "AtriClip",
        "Isolator Synergy",
        "EPi-Sense",
        "cryoICE",
    ],
    # Reference / own portfolio — useful for contrast, not email filtering
    "Johnson & Johnson": [
        "Biosense Webster",
        "VARIPULSE",
        "Varipulse",
        "CARTO",
        "QDOT MICRO",
        "QDOT",
        "THERMOCOOL",
        "ThermoCool",
        "OPTRELL",
        "Octaray",
        "OCTARAY",
        "NuVision",
    ],
}

# Company names used in the PubMed Boolean OR clause (competitors only)
COMPETITOR_COMPANIES: list[str] = [
    "Boston Scientific",
    "Medtronic",
    "Abbott",
    "MicroPort",
    "AtriCure",
]

# Flattened product keywords for PubMed query expansion
COMPETITOR_PRODUCTS: list[str] = [
    kw
    for company in COMPETITOR_COMPANIES
    for kw in COMPANY_PRODUCT_MAP.get(company, [])
]

# ---------------------------------------------------------------------------
# Research topic vocabularies (PubMed query building blocks)
# ---------------------------------------------------------------------------

ABLATION_TECHNOLOGIES: list[str] = [
    "Pulsed Field Ablation",
    "Radiofrequency Ablation",
    "Cryoballoon Ablation",
    "Cryoablation",
    "Laser Ablation",
    "Catheter Ablation",
]

CARDIAC_DISEASES: list[str] = [
    "Atrial Fibrillation",
    "Persistent Atrial Fibrillation",
    "Persistent AF",
    "Ventricular Tachycardia",
    "Supraventricular Tachycardia",
]

EP_TOPICS: list[str] = [
    "Electrophysiology",
    "Electroanatomic Mapping",
    "3D Mapping",
    "Contact Force",
    "High Density Mapping",
    "Intracardiac Echocardiography",
]

# Allowed classification labels returned by the LLM
TECHNOLOGY_LABELS: list[str] = ["PFA", "RF", "Cryo", "Mapping", "AI", "Other"]
DISEASE_LABELS: list[str] = ["AF", "Persistent AF", "VT", "SVT", "Other"]

# ---------------------------------------------------------------------------
# OpenAI system prompt
# ---------------------------------------------------------------------------

OPENAI_SYSTEM_PROMPT: str = (
    "You are a senior medical device competitive intelligence analyst "
    "specializing in cardiac electrophysiology."
)


@dataclass
class RuntimeConfig:
    """Optional runtime overrides passed from CLI / scheduler."""

    lookback_days: int = LOOKBACK_DAYS
    importance_threshold: int = IMPORTANCE_THRESHOLD
    schedule_mode: str = SCHEDULE_MODE
    dry_run: bool = False
    send_email: bool = True
    extra_companies: list[str] = field(default_factory=list)
