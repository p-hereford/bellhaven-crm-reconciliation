"""Approval-gated CRM ZIP correction, isolated from ownership and CHOW writers."""

import json
from urllib.request import Request, urlopen

from crm_writer import (
    BASE_URL,
    BELLHAVEN_PARENT_ID,
    _headers,
    get_account,
    load_json,
    normalise_street,
)


def apply_zip(proposal_id):
    """Correct only billing_zip after approval and verify the complete account."""
    proposals = load_json("proposals_address_draft.json")
    selected = [p for p in proposals if p.get("proposal_id") == proposal_id]
    if len(selected) != 1:
        raise ValueError("ZIP proposal not found or duplicated")
    proposal = selected[0]
    if proposal.get("action") != "FIX_ZIP":
        raise ValueError("This is not a ZIP correction proposal")

    decisions = load_json("review_decisions.json")
    if decisions.get(proposal_id) != "staged":
        raise ValueError("ZIP correction has not been approved and staged")

    account_id = proposal["crm_account_id"]
    headers = _headers()
    before = get_account(account_id, headers)
    website = proposal["website_address"]

    if before.get("account_id") != account_id:
        raise ValueError("CRM account identity mismatch")
    if before.get("name") != proposal["crm_name"]:
        raise ValueError("Account name changed since review")
    if before.get("parent_id") != BELLHAVEN_PARENT_ID or \
            proposal.get("current_parent_id") != BELLHAVEN_PARENT_ID:
        raise ValueError("Unexpected ownership change")
    if before.get("status") != "Active":
        raise ValueError("Account is no longer active")
    if before.get("duplicate_of_account") or before.get("chow_current_account"):
        raise ValueError("Duplicate/CHOW account cannot use this procedure")
    if normalise_street(before.get("billing_street")) != normalise_street(website["street"]):
        raise ValueError("Street evidence no longer matches")
    for field, source in (("billing_city", "city"), ("billing_state", "state")):
        if str(before.get(field, "")).strip().casefold() != str(website[source]).strip().casefold():
            raise ValueError(f"{field} differs from website evidence")
    if before.get("lifetime_revenue") != proposal["revenue"] or \
            before.get("outstanding_ar") != proposal["outstanding_ar"]:
        raise ValueError("Financial data changed since review")

    old_zip = str(proposal["current_zip"])
    target_zip = str(proposal["proposed_zip"])
    if target_zip != str(website["zip"]) or old_zip == target_zip:
        raise ValueError("Invalid ZIP correction proposal")
    live_zip = str(before.get("billing_zip", ""))
    if live_zip not in (old_zip, target_zip):
        raise ValueError("ZIP changed unexpectedly. New review required")

    if live_zip == old_zip:
        request = Request(
            f"{BASE_URL}/accounts/{account_id}",
            data=json.dumps({"billing_zip": target_zip}).encode("utf-8"),
            headers=headers,
            method="PATCH",
        )
        with urlopen(request, timeout=20) as response:
            response.read()

    after = get_account(account_id, headers)
    if str(after.get("billing_zip", "")) != target_zip:
        raise RuntimeError("ZIP change was not verified in live CRM")

    # The API may update its own audit timestamp, but nothing else may change.
    audit_fields = ("updated_at", "billing_zip")
    for key in set(before) | set(after):
        if key not in audit_fields and before.get(key) != after.get(key):
            raise RuntimeError(f"Unexpected CRM field changed: {key}")

    return f"Verified Portsmouth ZIP correction: {old_zip} → {target_zip}"
