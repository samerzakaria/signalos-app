# signalos_lib/product/intent.py
# Phase P1 - Product Intent Model (deterministic extraction)
#
# Converts a user prompt (and optional repo context from adoption) into a
# structured ProductIntent dict.  Pure stdlib - no LLM or network required.

from __future__ import annotations

__all__ = [
    "EMPTY_INTENT",
    "extract_product_intent",
    "load_intent",
    "refine_intent_with_llm",
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

# ---------------------------------------------------------------------------
# Role / actor words - these go to target_users, not entities
# ---------------------------------------------------------------------------

_ROLE_WORDS = {
    "doctor", "doctors", "nurse", "nurses", "admin", "admins",
    "staff", "user", "users", "manager", "managers",
    "operator", "operators", "provider", "providers",
    "patient", "patients", "client", "clients", "customer", "customers",
    "team", "teams", "member", "members", "owner", "owners",
    "viewer", "viewers", "editor", "editors",
    "developer", "developers", "employee", "employees",
    "student", "students", "teacher", "teachers",
    "vet", "vets", "veterinarian", "veterinarians",
    "technician", "technicians", "receptionist", "receptionists",
    "analyst", "analysts", "accountant", "accountants",
    "supervisor", "supervisors", "coordinator", "coordinators",
}

# Compound role phrases (matched as full phrase before splitting)
_ROLE_PHRASES = [
    "admin staff", "team members", "team member",
    "front desk", "help desk", "support staff",
]

# ---------------------------------------------------------------------------
# Workflow gerunds / verb-nouns - these indicate workflows, not entities
# ---------------------------------------------------------------------------

_WORKFLOW_GERUNDS = {
    "intake", "scheduling", "tracking", "monitoring", "recording",
    "reporting", "managing", "processing", "reviewing", "approving",
    "onboarding", "billing", "invoicing",
}

# Patterns: "X management" -> manage_X, "X tracking" -> track_X
_WORKFLOW_SUFFIX_MAP: dict[str, str] = {
    "management": "manage",
    "tracking": "track",
    "monitoring": "monitor",
    "reporting": "report",
    "processing": "process",
    "scheduling": "schedule",
    "recording": "record",
    "reviewing": "review",
    "approving": "approve",
}

# ---------------------------------------------------------------------------
# Singularization (simple suffix rules, no external deps)
# ---------------------------------------------------------------------------

_PLURAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ies$"), "y"),       # categories -> category
    (re.compile(r"ses$"), "s"),       # addresses -> address
    (re.compile(r"([^s])s$"), r"\1"), # tasks -> task
]


def _singularize(word: str) -> str:
    """Best-effort singularization (no external library)."""
    lower = word.lower()
    # Don't singularize short words or words that don't end in 's'
    if len(lower) <= 3 or not lower.endswith("s"):
        return word
    # Keep words that are already singular-looking
    if lower in {"status", "class", "bus", "alias", "canvas", "access",
                 "address", "process", "analysis", "basis", "series"}:
        return word
    for pat, repl in _PLURAL_RULES:
        result, n = pat.subn(repl, lower)
        if n > 0:
            return result
    return word


def _to_pascal_case(phrase: str) -> str:
    """Convert a phrase to PascalCase: 'lab results' -> 'LabResult'."""
    words = phrase.strip().split()
    # Singularize the last word (the head noun)
    if words:
        words[-1] = _singularize(words[-1])
    return "".join(w.capitalize() for w in words)


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


# Phrases that are security/auth/audit concerns, not domain entities
_QUALIFIER_WORDS = {
    "veterinary", "corporate", "enterprise", "personal",
    "internal", "external", "online", "digital", "virtual",
    "clinic", "clinics", "hospital", "hospitals", "office", "offices",
    "company", "companies", "agency", "agencies", "firm", "firms",
}

_NON_ENTITY_PHRASES = [
    re.compile(r"\brole.based\s+access\b", re.I),
    re.compile(r"\baccess\s+control\b", re.I),
    re.compile(r"\baudit\s+trail\b", re.I),
    re.compile(r"\baudit\s+log\b", re.I),
    re.compile(r"\bcompliance\b", re.I),
    re.compile(r"\bhipaa\b", re.I),
    re.compile(r"\bgdpr\b", re.I),
    re.compile(r"\bsoc\s*2\b", re.I),
    re.compile(r"\bpci\b", re.I),
    re.compile(r"\brbac\b", re.I),
    re.compile(r"\bpermission\b", re.I),
    re.compile(r"\bauthentication\b", re.I),
    re.compile(r"\bauthorization\b", re.I),
]


def _is_non_entity(phrase: str) -> bool:
    """Return True if the phrase is a security/auth/audit term, not a domain entity."""
    for pat in _NON_ENTITY_PHRASES:
        if pat.search(phrase):
            return True
    return False


def _classify_entities(
    raw_entities: list[str],
    text: str,
) -> tuple[list[str], list[str], list[str]]:
    """Split raw entities into (entities, target_users, workflows).

    Returns PascalCase entities, lowercase role names, and workflow strings.
    """
    final_entities: list[str] = []
    extra_users: list[str] = []
    extra_workflows: list[str] = []

    for raw in raw_entities:
        lower = raw.lower().strip()

        # Skip security/auth/audit phrases - not domain entities
        if _is_non_entity(lower):
            continue

        # Check for compound role phrases first
        if lower in [rp.lower() for rp in _ROLE_PHRASES]:
            extra_users.append(lower)
            continue

        # Check for "X management/tracking" workflow pattern
        words = lower.split()
        if len(words) >= 2 and words[-1] in _WORKFLOW_SUFFIX_MAP:
            verb = _WORKFLOW_SUFFIX_MAP[words[-1]]
            obj = " ".join(words[:-1])
            extra_workflows.append(f"{verb}_{obj.replace(' ', '_')}")
            # The object noun is still an entity
            pascal = _to_pascal_case(obj)
            if pascal:
                final_entities.append(pascal)
            continue

        # Check if it's a standalone workflow gerund
        if lower in _WORKFLOW_GERUNDS:
            extra_workflows.append(lower)
            continue

        # Check if the phrase is entirely role words
        all_role = all(w in _ROLE_WORDS for w in words)
        if all_role:
            extra_users.append(lower)
            continue

        # Filter out role words and qualifier words, keep the rest as entity
        non_role = [w for w in words if w not in _ROLE_WORDS and w not in _QUALIFIER_WORDS]
        role_words_found = [w for w in words if w in _ROLE_WORDS]

        # If some words were roles, add them as users
        if role_words_found:
            for rw in role_words_found:
                extra_users.append(rw)

        # Detect embedded workflow gerunds in compound nouns
        entity_words = []
        for w in non_role:
            if w in _WORKFLOW_GERUNDS:
                extra_workflows.append(w)
            else:
                entity_words.append(w)

        if entity_words:
            pascal = _to_pascal_case(" ".join(entity_words))
            if pascal:
                final_entities.append(pascal)

    return (
        _dedup(final_entities),
        _dedup(extra_users),
        _dedup(extra_workflows),
    )


# ---------------------------------------------------------------------------
# Workflow extraction (verb phrases)
# ---------------------------------------------------------------------------

_WORKFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(create|add|edit|delete|update|assign|complete|archive|view|list|filter|"
        r"search|sort|export|import|invite|login|register|sign\s*up|log\s*out|"
        r"share|publish|approve|reject|submit|review|upload|download|"
        r"monitor|analyze|report|visualize|track|schedule|notify|alert|"
        r"manage|record|process)\b"
        r"\s+([\w\s]+?)(?:\.|,|;|$)",
        re.I,
    ),
]

# Standalone gerund pattern: "patient intake", "provider scheduling"
_GERUND_PHRASE_PATTERN = re.compile(
    r"\b(\w+)\s+(intake|scheduling|tracking|monitoring|recording|reporting|"
    r"managing|processing|reviewing|approving|onboarding|billing|invoicing)\b",
    re.I,
)


def _extract_workflows(text: str) -> list[str]:
    workflows: list[str] = []
    # Verb + object patterns
    for pat in _WORKFLOW_PATTERNS:
        for m in pat.finditer(text):
            verb = m.group(1).strip().lower()
            obj = m.group(2).strip().rstrip(".")
            words = obj.lower().split()
            meaningful = [w for w in words if w not in _NOISE_WORDS]
            if meaningful:
                workflows.append(f"{verb} {' '.join(meaningful)}")
    # Gerund phrases: "patient intake" -> "intake patient"
    for m in _GERUND_PHRASE_PATTERN.finditer(text):
        obj = m.group(1).strip().lower()
        gerund = m.group(2).strip().lower()
        if obj not in _NOISE_WORDS:
            workflows.append(f"{gerund} {obj}")
    return _dedup(workflows)


# ---------------------------------------------------------------------------
# Surface detection
# ---------------------------------------------------------------------------

_UX_SURFACE_MAP: dict[str, re.Pattern[str]] = {
    "dashboard": re.compile(r"\bdashboard\b", re.I),
    "form": re.compile(r"\bform\b", re.I),
    "list": re.compile(r"\blist\b", re.I),
    "table": re.compile(r"\btable\b|\bgrid\b|\brecords?\b", re.I),
    "detail": re.compile(r"\bdetail\b|\bdetails\b", re.I),
    "view": re.compile(r"\bview\b", re.I),
    "page": re.compile(r"\bpage\b", re.I),
    "panel": re.compile(r"\bpanel\b", re.I),
    "chart": re.compile(r"\bchart\b|\bgraph\b|\bvisuali[sz]", re.I),
    "report": re.compile(r"\breport\b|\breports\b|\breporting\b", re.I),
    "search": re.compile(r"\bsearch\b", re.I),
    "modal": re.compile(r"\bmodal\b|\bdialog\b|\bpopup\b", re.I),
    "kanban": re.compile(r"\bkanban\b|\bboard\b", re.I),
    "calendar": re.compile(r"\bcalendar\b|\bschedul", re.I),
    "timeline": re.compile(r"\btimeline\b|\bgantt\b", re.I),
    "inbox": re.compile(r"\binbox\b", re.I),
    "feed": re.compile(r"\bfeed\b", re.I),
    "card": re.compile(r"\bcard\b|\bcards\b", re.I),
    "notification": re.compile(r"\bnotification\b|\balert\b|\btoast\b", re.I),
    "settings": re.compile(r"\bsettings\b|\bpreference\b|\bconfig\b", re.I),
    "profile": re.compile(r"\bprofile\b|\baccount\b", re.I),
}

# Domain patterns that imply multiple UX surfaces
_DOMAIN_SURFACE_MAP: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\brecords?\s+system\b|\brecord\s+keeping\b", re.I),
     ["table", "detail", "search"]),
    (re.compile(r"\bschedul", re.I),
     ["calendar", "form"]),
    (re.compile(r"\binventory\b", re.I),
     ["table", "search", "form"]),
]

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


_SECURITY_MAP: dict[str, re.Pattern[str]] = {
    "hipaa": re.compile(r"\bHIPAA\b"),
    "gdpr": re.compile(r"\bGDPR\b"),
    "soc2": re.compile(r"\bSOC\s*2\b", re.I),
    "pci": re.compile(r"\bPCI\b"),
    "compliance": re.compile(r"\bcompliance\b", re.I),
}

_AUDIT_MAP: dict[str, re.Pattern[str]] = {
    "audit-trail": re.compile(r"\baudit\s+trail\b", re.I),
    "audit-log": re.compile(r"\baudit\s+log\b", re.I),
}


def _detect_surfaces(text: str, surface_map: dict[str, re.Pattern[str]]) -> list[str]:
    return [name for name, pat in surface_map.items() if pat.search(text)]


def _detect_ux_surfaces(text: str) -> list[str]:
    """Detect UX surfaces from explicit keywords and domain patterns."""
    surfaces = _detect_surfaces(text, _UX_SURFACE_MAP)
    # Add domain-implied surfaces
    for pat, implied in _DOMAIN_SURFACE_MAP:
        if pat.search(text):
            for s in implied:
                if s not in surfaces:
                    surfaces.append(s)
    return surfaces


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
    # "include doctors, nurses, and admin staff"
    re.compile(
        r"\b(?:include|includes|including)\s+(.+?)(?:\.|$)",
        re.I,
    ),
]


def _extract_target_users(text: str) -> list[str]:
    users: list[str] = []
    for pat in _USER_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).strip() if pat.groups else m.group(0).strip()
            # For "include X, Y, and Z" patterns, split on commas/and
            parts = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", raw)
            for part in parts:
                cleaned = part.strip().rstrip(".").lower()
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

    Deterministic - keyword and pattern matching only, no LLM.
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

    # Extract raw entities, then classify into entities / users / workflows
    raw_entities = _extract_entities(text)
    classified_entities, extra_users, extra_workflows = _classify_entities(
        raw_entities, text,
    )
    intent["entities"] = classified_entities

    # Merge extra users and workflows from entity classification
    for u in extra_users:
        if u not in intent["target_users"]:
            intent["target_users"].append(u)
    for w in extra_workflows:
        if w not in intent["primary_workflows"]:
            intent["primary_workflows"].append(w)

    intent["ux_surfaces"] = _detect_ux_surfaces(text)
    intent["api_surfaces"] = _detect_surfaces(text, _API_SURFACE_MAP)
    intent["data_sources"] = _detect_surfaces(text, _DATA_SOURCE_MAP)
    intent["auth_requirements"] = _detect_surfaces(text, _AUTH_MAP)

    # Security and audit detection
    intent["security_constraints"] = _detect_surfaces(text, _SECURITY_MAP)
    intent["audit_requirements"] = _detect_surfaces(text, _AUDIT_MAP)

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


# ---------------------------------------------------------------------------
# LLM refinement (optional -- requires API key)
# ---------------------------------------------------------------------------

_REFINE_PROMPT = """\
You are a product architect. Given a user's product description and a \
deterministic extraction, refine ALL fields.

Rules:
- entities: domain objects in PascalCase singular (Patient, Task). Remove \
  location/context qualifiers ("veterinary clinics pet" -> "Pet"). If a word \
  is both a user AND entity (Patient in medical app), include in BOTH lists.
- target_users: roles/actors in lowercase (doctor, admin, front desk).
- primary_workflows: user actions in snake_case (create_task, schedule_appointment).
- product_type: one of "task-management", "financial-dashboard", "e-commerce", \
  "social", "cms", "analytics", "custom", or a short descriptive slug.
- ux_surfaces: UI patterns needed (dashboard, table, form, detail, list, chart, \
  calendar, search, kanban, timeline, inbox, feed, map, settings).
- security_constraints: compliance/security requirements (hipaa, gdpr, soc2, pci, \
  encryption-at-rest, mfa) -- only if explicitly stated or strongly implied.
- audit_requirements: audit needs (audit-trail, audit-log, change-history).
- pii_entities: which entities contain personally identifiable information.
- Do NOT invent features not implied by the prompt.

Prompt: {prompt}

Current extraction:
  entities: {entities}
  target_users: {users}
  workflows: {workflows}
  product_type: {product_type}
  ux_surfaces: {surfaces}
  security_constraints: {security}
  audit_requirements: {audit}

Return ONLY valid JSON (no markdown, no explanation):
{{"entities": [...], "target_users": [...], "workflows": [...], \
"product_type": "...", "ux_surfaces": [...], "security_constraints": [...], \
"audit_requirements": [...], "pii_entities": [...]}}
"""


def refine_intent_with_llm(
    intent: dict[str, Any],
    prompt: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Refine extracted intent using a single LLM call.

    Falls back to the original intent if the call fails or no API key
    is configured.  Never crashes -- returns the input unchanged on error.
    """
    try:
        from signalos_lib.harness import _resolve_provider, DEFAULT_MODEL
    except ImportError:
        return intent

    llm_prompt = _REFINE_PROMPT.format(
        prompt=prompt,
        entities=json.dumps(intent.get("entities", [])),
        users=json.dumps(intent.get("target_users", [])),
        workflows=json.dumps(intent.get("primary_workflows", [])),
        product_type=intent.get("product_type", "custom"),
        surfaces=json.dumps(intent.get("ux_surfaces", [])),
        security=json.dumps(intent.get("security_constraints", [])),
        audit=json.dumps(intent.get("audit_requirements", [])),
    )

    try:
        provider = _resolve_provider(provider_name)
        response_text, _tok_in, _tok_out = provider.call(
            llm_prompt, model or DEFAULT_MODEL,
        )
    except Exception:
        return intent

    # Parse the JSON response
    try:
        # Strip markdown fences if present
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        refined = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return intent

    if not isinstance(refined, dict):
        return intent

    # Merge refinements back into intent -- only override with non-empty lists
    updated = dict(intent)
    _LIST_FIELDS = {
        "entities": "entities",
        "target_users": "target_users",
        "workflows": "primary_workflows",
        "ux_surfaces": "ux_surfaces",
        "security_constraints": "security_constraints",
        "audit_requirements": "audit_requirements",
        "pii_entities": "pii_entities",
    }
    for llm_key, intent_key in _LIST_FIELDS.items():
        val = refined.get(llm_key)
        if isinstance(val, list) and val:
            updated[intent_key] = val

    if refined.get("product_type") and isinstance(refined["product_type"], str):
        updated["product_type"] = refined["product_type"]

    return updated
