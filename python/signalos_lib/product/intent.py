# signalos_lib/product/intent.py
# Phase P1 — Product Intent Model (deterministic extraction)
#
# Converts a user prompt (and optional repo context from adoption) into a
# structured ProductIntent dict.  Pure stdlib — no LLM or network required.

from __future__ import annotations

__all__ = [
    "EMPTY_INTENT",
    "extract_product_intent",
    "load_intent",
    "write_intent",
]

import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Empty intent template
# ---------------------------------------------------------------------------

def _empty_intent() -> dict[str, Any]:
    return {
        "product_name": "",
        "product_type": "",
        "target_users": [],
        "primary_workflows": [],
        "entities": [],
        "entity_relationships": [],
        "ux_surfaces": [],
        "api_surfaces": [],
        "data_sources": [],
        "integrations": [],
        "auth_requirements": [],
        "permissions": [],
        "audit_requirements": [],
        "security_constraints": [],
        "performance_expectations": [],
        "deployment_intent": "none",
        "stack_preferences": [],
        "unknowns": [],
        "assumptions": [],
        "out_of_scope": [],
    }


EMPTY_INTENT = _empty_intent()


# ---------------------------------------------------------------------------
# Product-type detection
# ---------------------------------------------------------------------------

_PRODUCT_TYPE_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    ("task-management", [
        re.compile(r"\btask\b.*\bmanag", re.I),
        re.compile(r"\bproject\s+manag", re.I),
        re.compile(r"\btodo\b|\bto-do\b", re.I),
        re.compile(r"\bkanban\b|\bboard\b.*\btask", re.I),
    ]),
    ("financial-dashboard", [
        re.compile(r"\bfinancial\b.*\bdashboard\b", re.I),
        re.compile(r"\brevenue\b|\bchurn\b|\brunway\b", re.I),
        re.compile(r"\bfinance\b.*\b(track|report|analyt)", re.I),
    ]),
    ("e-commerce", [
        re.compile(r"\be-?commerce\b|\bonline\s+store\b|\bshop\b", re.I),
        re.compile(r"\bcart\b.*\b(checkout|product)", re.I),
    ]),
    ("social-platform", [
        re.compile(r"\bsocial\b.*\b(network|media|platform)\b", re.I),
        re.compile(r"\bfeed\b.*\b(post|follow)", re.I),
    ]),
    ("crm", [
        re.compile(r"\bcrm\b|\bcustomer\s+relationship\b", re.I),
        re.compile(r"\blead\b.*\b(track|manag|pipeline)", re.I),
    ]),
    ("dashboard", [
        re.compile(r"\bdashboard\b", re.I),
        re.compile(r"\bmetric\b.*\b(visual|chart|graph)", re.I),
    ]),
]


def _detect_product_type(text: str) -> str:
    best_type = ""
    best_hits = 0
    for ptype, patterns in _PRODUCT_TYPE_RULES:
        hits = sum(1 for p in patterns if p.search(text))
        if hits > best_hits:
            best_hits = hits
            best_type = ptype
    return best_type if best_hits > 0 else "custom"


# ---------------------------------------------------------------------------
# Entity extraction (noun phrases)
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    # "with X, Y, and Z" or "for X, Y, and Z" after build/create/manage
    re.compile(
        r"\b(?:with|including|has|have|contain|for)\b\s+(.+?)(?:\.|$)",
        re.I,
    ),
    # "manage X" / "track X"
    re.compile(
        r"\b(?:manage|track|organize|handle|store)\b\s+(\w[\w\s,]+?)(?:\.|,\s*and\b|$)",
        re.I,
    ),
]

_NOISE_WORDS = {
    "a", "an", "the", "my", "me", "i", "for", "to", "of", "in", "on",
    "it", "is", "that", "this", "some", "and", "or", "with", "app",
    "application", "tool", "system", "platform", "something", "cool",
    "stuff", "things",
}


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for pat in _ENTITY_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1)
            # Split on commas / "and"
            parts = re.split(r"\s*,\s*|\s+and\s+", raw)
            for part in parts:
                cleaned = part.strip().strip(".")
                # Keep only multi-word or meaningful single-word phrases
                words = cleaned.lower().split()
                meaningful = [w for w in words if w not in _NOISE_WORDS]
                if meaningful:
                    entities.append(" ".join(meaningful))
    return _dedup(entities)


# ---------------------------------------------------------------------------
# Workflow extraction (verb phrases)
# ---------------------------------------------------------------------------

_WORKFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(create|add|edit|delete|update|assign|complete|archive|view|list|filter|"
        r"search|sort|export|import|invite|login|register|sign\s*up|log\s*out|"
        r"share|publish|approve|reject|submit|review|upload|download|"
        r"monitor|analyze|report|visualize|track|schedule|notify|alert)\b"
        r"\s+([\w\s]+?)(?:\.|,|;|$)",
        re.I,
    ),
]


def _extract_workflows(text: str) -> list[str]:
    workflows: list[str] = []
    for pat in _WORKFLOW_PATTERNS:
        for m in pat.finditer(text):
            verb = m.group(1).strip().lower()
            obj = m.group(2).strip().rstrip(".")
            words = obj.lower().split()
            meaningful = [w for w in words if w not in _NOISE_WORDS]
            if meaningful:
                workflows.append(f"{verb} {' '.join(meaningful)}")
    return _dedup(workflows)


# ---------------------------------------------------------------------------
# Surface detection
# ---------------------------------------------------------------------------

_UX_SURFACE_MAP: dict[str, re.Pattern[str]] = {
    "dashboard": re.compile(r"\bdashboard\b", re.I),
    "form": re.compile(r"\bform\b", re.I),
    "list": re.compile(r"\blist\b|\btable\b", re.I),
    "table": re.compile(r"\btable\b|\bgrid\b", re.I),
    "chart": re.compile(r"\bchart\b|\bgraph\b|\bvisuali[sz]", re.I),
    "modal": re.compile(r"\bmodal\b|\bdialog\b|\bpopup\b", re.I),
    "kanban": re.compile(r"\bkanban\b|\bboard\b", re.I),
    "calendar": re.compile(r"\bcalendar\b", re.I),
    "timeline": re.compile(r"\btimeline\b|\bgantt\b", re.I),
    "notification": re.compile(r"\bnotification\b|\balert\b|\btoast\b", re.I),
    "settings": re.compile(r"\bsettings\b|\bpreference\b|\bconfig\b", re.I),
    "profile": re.compile(r"\bprofile\b|\baccount\b", re.I),
}

_API_SURFACE_MAP: dict[str, re.Pattern[str]] = {
    "rest-api": re.compile(r"\bREST\b|\bapi\b|\bendpoint\b", re.I),
    "graphql": re.compile(r"\bgraphql\b", re.I),
    "websocket": re.compile(r"\bwebsocket\b|\brealtime\b|\breal-time\b", re.I),
    "webhook": re.compile(r"\bwebhook\b", re.I),
}

_DATA_SOURCE_MAP: dict[str, re.Pattern[str]] = {
    "database": re.compile(r"\bdatabase\b|\bdb\b|\bsql\b|\bpostgres\b|\bmysql\b|\bsqlite\b|\bmongo\b", re.I),
    "csv": re.compile(r"\bcsv\b|\bspreadsheet\b|\bexcel\b", re.I),
    "external-api": re.compile(r"\bexternal\s+api\b|\bthird.party\b|\bintegrat", re.I),
    "file-storage": re.compile(r"\bfile\s+(storage|upload)\b|\bs3\b|\bblob\b", re.I),
    "cache": re.compile(r"\bcache\b|\bredis\b|\bmemcache\b", re.I),
}

_AUTH_MAP: dict[str, re.Pattern[str]] = {
    "login": re.compile(r"\blogin\b|\blog\s*in\b|\bsign\s*in\b", re.I),
    "registration": re.compile(r"\bregist(er|ration)\b|\bsign\s*up\b", re.I),
    "oauth": re.compile(r"\boauth\b|\bsso\b|\bsingle\s+sign\b", re.I),
    "api-key": re.compile(r"\bapi.key\b|\btoken\b", re.I),
    "rbac": re.compile(r"\brole\b.*\baccess\b|\brbac\b|\bpermission\b", re.I),
    "auth": re.compile(r"\bauth(entication|orization)?\b", re.I),
}

_DEPLOYMENT_MAP: dict[str, re.Pattern[str]] = {
    "docker": re.compile(r"\bdocker\b|\bcontainer\b", re.I),
    "cloud": re.compile(r"\baws\b|\bazure\b|\bgcp\b|\bcloud\b|\bheroku\b|\bvercel\b|\bnetlify\b", re.I),
    "self-hosted": re.compile(r"\bself.hosted\b|\bon.prem", re.I),
    "serverless": re.compile(r"\bserverless\b|\blambda\b|\bedge\b", re.I),
}

_STACK_PATTERNS: dict[str, re.Pattern[str]] = {
    "react": re.compile(r"\breact\b", re.I),
    "vue": re.compile(r"\bvue\b", re.I),
    "svelte": re.compile(r"\bsvelte\b", re.I),
    "angular": re.compile(r"\bangular\b", re.I),
    "vite": re.compile(r"\bvite\b", re.I),
    "next": re.compile(r"\bnext\.?js\b|\bnext\b", re.I),
    "python": re.compile(r"\bpython\b", re.I),
    "fastapi": re.compile(r"\bfastapi\b|\bfast\s*api\b", re.I),
    "django": re.compile(r"\bdjango\b", re.I),
    "flask": re.compile(r"\bflask\b", re.I),
    "node": re.compile(r"\bnode\.?js\b|\bnode\b|\bexpress\b", re.I),
    "typescript": re.compile(r"\btypescript\b|\bts\b", re.I),
    "rust": re.compile(r"\brust\b|\btauri\b", re.I),
    "go": re.compile(r"\bgo\s*lang\b|\bgo\b", re.I),
    "tailwind": re.compile(r"\btailwind\b", re.I),
    "postgres": re.compile(r"\bpostgres\b|\bpostgresql\b", re.I),
    "mysql": re.compile(r"\bmysql\b", re.I),
    "mongodb": re.compile(r"\bmongo\b|\bmongodb\b", re.I),
    "sqlite": re.compile(r"\bsqlite\b", re.I),
}


def _detect_surfaces(text: str, surface_map: dict[str, re.Pattern[str]]) -> list[str]:
    return [name for name, pat in surface_map.items() if pat.search(text)]


def _detect_deployment(text: str) -> str:
    for name, pat in _DEPLOYMENT_MAP.items():
        if pat.search(text):
            return name
    return "none"


# ---------------------------------------------------------------------------
# Product name extraction
# ---------------------------------------------------------------------------

_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'\b(?:called|named)\s+"([^"]+)"', re.I),
    re.compile(r"\b(?:called|named)\s+(\w[\w\s-]{0,30}\w)", re.I),
    re.compile(r'\bbuild\b.*\b(?:a|an|the)\s+(.+?)(?:\s+(?:app|application|platform|tool|system|for|with|that|using)\b)', re.I),
]


def _extract_product_name(text: str) -> str:
    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip().rstrip(".")
            words = name.split()
            # Skip if it's just noise
            meaningful = [w for w in words if w.lower() not in _NOISE_WORDS]
            if meaningful:
                return " ".join(words[:5])  # Cap at 5 words
    return ""


# ---------------------------------------------------------------------------
# Target user extraction
# ---------------------------------------------------------------------------

_USER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfor\s+([\w\s]+?)\s*(?:to|who|that)\b", re.I),
    re.compile(r"\b(team\s*members?|admins?|managers?|developers?|users?|customers?|clients?|employees?|students?|teachers?)\b", re.I),
]


def _extract_target_users(text: str) -> list[str]:
    users: list[str] = []
    for pat in _USER_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).strip() if pat.groups else m.group(0).strip()
            cleaned = raw.lower().strip()
            if cleaned and cleaned not in _NOISE_WORDS and len(cleaned) > 2:
                users.append(cleaned)
    return _dedup(users)


# ---------------------------------------------------------------------------
# Integration extraction
# ---------------------------------------------------------------------------

_INTEGRATION_PATTERNS: dict[str, re.Pattern[str]] = {
    "slack": re.compile(r"\bslack\b", re.I),
    "github": re.compile(r"\bgithub\b", re.I),
    "jira": re.compile(r"\bjira\b", re.I),
    "stripe": re.compile(r"\bstripe\b", re.I),
    "twilio": re.compile(r"\btwilio\b", re.I),
    "sendgrid": re.compile(r"\bsendgrid\b", re.I),
    "email": re.compile(r"\bemail\b|\bsmtp\b", re.I),
    "google": re.compile(r"\bgoogle\b", re.I),
    "aws": re.compile(r"\baws\b|\bs3\b", re.I),
}


# ---------------------------------------------------------------------------
# Repo-context merge
# ---------------------------------------------------------------------------

def _merge_repo_context(intent: dict[str, Any], repo_context: dict[str, Any]) -> None:
    """Merge surfaces detected from an adopted repo into the intent."""
    surfaces = repo_context.get("surface_inventory", {}).get("surfaces", [])
    for surface in surfaces:
        stype = surface.get("type", "")
        if stype == "frontend" and "web-ui" not in intent["ux_surfaces"]:
            intent["ux_surfaces"].append("web-ui")
        elif stype == "tauri" and "desktop-app" not in intent["ux_surfaces"]:
            intent["ux_surfaces"].append("desktop-app")
        elif stype == "python" and "python" not in intent["stack_preferences"]:
            intent["stack_preferences"].append("python")
        elif stype == "rust" and "rust" not in intent["stack_preferences"]:
            intent["stack_preferences"].append("rust")
        elif stype == "ci" and "ci-pipeline" not in intent["data_sources"]:
            intent["data_sources"].append("ci-pipeline")
        elif stype == "deployment" and intent["deployment_intent"] == "none":
            intent["deployment_intent"] = "docker"
        elif stype == "tests" and "automated-tests" not in intent["audit_requirements"]:
            intent["audit_requirements"].append("automated-tests")

    # Merge detected profile into stack
    profile = repo_context.get("surface_inventory", {}).get("detected_profile", "")
    if profile == "react-vite":
        for pref in ("react", "vite"):
            if pref not in intent["stack_preferences"]:
                intent["stack_preferences"].append(pref)

    # Merge project name if empty
    if not intent["product_name"]:
        name = repo_context.get("surface_inventory", {}).get("project_name", "")
        if name:
            intent["product_name"] = name


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_product_intent(
    prompt: str,
    repo_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract structured product intent from a prompt string.

    Deterministic — keyword and pattern matching only, no LLM.
    If *repo_context* is provided (e.g. from adoption scanning), detected
    surfaces are merged into the intent.
    """
    intent = _empty_intent()
    text = prompt.strip()

    if not text:
        return intent

    intent["product_name"] = _extract_product_name(text)
    intent["product_type"] = _detect_product_type(text)
    intent["target_users"] = _extract_target_users(text)
    intent["primary_workflows"] = _extract_workflows(text)
    intent["entities"] = _extract_entities(text)
    intent["ux_surfaces"] = _detect_surfaces(text, _UX_SURFACE_MAP)
    intent["api_surfaces"] = _detect_surfaces(text, _API_SURFACE_MAP)
    intent["data_sources"] = _detect_surfaces(text, _DATA_SOURCE_MAP)
    intent["auth_requirements"] = _detect_surfaces(text, _AUTH_MAP)
    intent["integrations"] = [
        name for name, pat in _INTEGRATION_PATTERNS.items()
        if pat.search(text)
    ]
    intent["deployment_intent"] = _detect_deployment(text)
    intent["stack_preferences"] = [
        name for name, pat in _STACK_PATTERNS.items()
        if pat.search(text)
    ]

    if repo_context is not None:
        _merge_repo_context(intent, repo_context)

    return intent


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_intent(intent: dict[str, Any], signalos_dir: Path) -> Path:
    """Write intent to ``.signalos/product/INTENT.json``."""
    product_dir = signalos_dir / "product"
    product_dir.mkdir(parents=True, exist_ok=True)
    path = product_dir / "INTENT.json"
    path.write_text(
        json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_intent(signalos_dir: Path) -> dict[str, Any] | None:
    """Read intent from ``.signalos/product/INTENT.json``, or None."""
    path = signalos_dir / "product" / "INTENT.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
