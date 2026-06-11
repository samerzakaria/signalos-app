"""Tests for the SaaS Subscription Starter blueprint (Stripe billing + auth)."""

from __future__ import annotations

import json
from pathlib import Path

from signalos_lib.product.blueprints.registry import (
    load_blueprint,
    list_blueprints,
    match_blueprint,
    validate_blueprint,
)

_BP_DIR = Path(__file__).resolve().parent / "signalos_lib" / "product" / "blueprints" / "saas-billing"


def test_registered_and_loads():
    assert "saas-billing" in [e["id"] for e in list_blueprints()]
    bp = load_blueprint("saas-billing")
    assert bp is not None
    assert bp["id"] == "saas-billing"
    assert bp["display_name"] == "SaaS Subscription Starter"


def test_validates_clean():
    bp = load_blueprint("saas-billing")
    assert bp is not None
    assert validate_blueprint(bp) == []


def test_covers_stripe_billing_and_auth():
    text = json.dumps(load_blueprint("saas-billing"), sort_keys=True).lower()
    # Billing (Stripe) coverage
    for term in ("stripe", "subscription", "invoice", "checkout", "webhook"):
        assert term in text, f"missing billing term: {term}"
    # Auth coverage
    for term in ("auth", "login", "signup", "session"):
        assert term in text, f"missing auth term: {term}"


def test_matches_billing_intent():
    # match_blueprint returns the matched blueprint id (str) or None.
    matched = match_blueprint({
        "product_type": "saas-billing",
        "raw_prompt": "a subscription SaaS with Stripe billing and login",
    })
    assert matched == "saas-billing"


def test_no_card_data_stored():
    # Security posture: card data must never be stored locally (PCI handled by Stripe).
    bp = load_blueprint("saas-billing")
    assert "no-card-data-stored" in bp["security"]
    entity_fields = json.dumps(bp["entities"]).lower()
    for forbidden in ("card_number", "cvv", "cvc", "card_cvc"):
        assert forbidden not in entity_fields
