"""Reviewer-gated, recoverable creation of genuinely missing Bellhaven CRM accounts.

This module does not modify historical accounts. It creates one new account
only after a staged human decision, then independently verifies the live CRM.
"""

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

import crm_writer as crm

PROJECT = Path(__file__).resolve().parent
JOURNAL_NAME = "create_journal.json"

# Verified against existing, correctly matched CRM facilities.
CARE_TYPES = {
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Assisted Living": "Assisted Living",
}


def _read(filename):
    with open(PROJECT / filename, encoding="utf-8") as file:
        return json.load(file)


def _save_journal(journal):
    target = PROJECT / JOURNAL_NAME
    temp = PROJECT / (JOURNAL_NAME + ".tmp")
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(journal, file, indent=2, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, target)


def _journal():
    path = PROJECT / JOURNAL_NAME
    return _read(JOURNAL_NAME) if path.exists() else {}


def _approved(proposal_id):
    proposals = [
        p for p in _read("proposals_missing_draft.json")
        if p.get("proposal_id") == proposal_id
    ]
    if len(proposals) != 1:
        raise ValueError("CREATE proposal missing or duplicated")
    p = proposals[0]
    if p.get("action") != "CREATE" or p.get("crm_account_id") is not None:
        raise ValueError("Only missing-account CREATE proposals are supported")
    if _read("review_decisions.json").get(proposal_id) != "staged":
        raise ValueError("CREATE proposal has not been approved")
    if p.get("proposed_parent_id") != crm.BELLHAVEN_PARENT_ID:
        raise ValueError("Unexpected proposed parent")
    if not p.get("source_url", "").startswith(
        "https://analyst-assessment-production.up.railway.app/communities/"
    ):
        raise ValueError("Unrecognised evidence URL")
    if not proposal_id.startswith("create:"):
        raise ValueError("Unexpected proposal identifier")
    if proposal_id != "create:" + p["source_url"].rstrip("/").split("/")[-1]:
        raise ValueError("Proposal ID differs from evidence URL")

    # Reject stale/tampered proposal details before a write.
    matches = [
        f for f in _read("bellhaven_facilities.json")
        if f.get("source_url") == p["source_url"]
    ]
    if len(matches) != 1:
        raise ValueError("Website evidence is missing or ambiguous")
    evidence = matches[0]
    if p["facility_name"] != evidence["name"] or p["proposed_name"] != evidence["name"]:
        raise ValueError("Proposed name disagrees with saved website evidence")
    expected_location = {
        "street": evidence["street"], "city": evidence["city"],
        "state": evidence["state"], "zip": evidence["zip"],
    }
    if p["website_address"] != expected_location:
        raise ValueError("Proposed location differs from saved website evidence")
    if p["care_offerings"] != evidence["care_offerings"]:
        raise ValueError("Proposed care offerings disagree with website evidence")
    if len(evidence["care_offerings"]) != 1:
        raise ValueError("Multiple care offerings require explicit manual mapping")
    care = CARE_TYPES.get(evidence["care_offerings"][0])
    if not care or p["proposed_care_type"] != care:
        raise ValueError("Proposed care classification not supported by evidence")
    return p


def _fingerprint(p):
    return hashlib.sha256(
        json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _same_location(account, p):
    return crm._same_location(account, p["website_address"])


def _marker(proposal_id):
    return f"Bellhaven CREATE proposal {proposal_id}."


def _live_candidates(accounts, p):
    """Find exactly one already-created record, and reject all ambiguities."""
    marker = _marker(p["proposal_id"])
    marked = [a for a in accounts if marker in str(a.get("note") or "")]
    same_address = [a for a in accounts if _same_location(a, p)]

    if len(marked) > 1:
        raise RuntimeError("Multiple accounts contain this creation marker")
    if len(same_address) > 1:
        raise RuntimeError("Multiple CRM accounts exist at proposed location")
    if marked:
        candidate = marked[0]
        if len(same_address) != 1 or same_address[0]["account_id"] != candidate["account_id"]:
            raise RuntimeError("Creation marker and address conflict")
        return candidate["account_id"]
    if same_address:
        raise RuntimeError(
            "CRM account already exists at this location without our creation marker. "
            "Manual review required; no POST sent."
        )
    return None


def _validate_other_accounts(accounts, p, existing_id):
    """Confirm known collisions and nearby unrelated accounts are still distinct."""
    others = [a for a in accounts if a["account_id"] != existing_id]
    actual_collisions = {
        a["account_id"]: a for a in others
        if a.get("name", "").casefold() == p["proposed_name"].casefold()
    }
    expected_collisions = {
        a["account_id"]: a for a in p["same_name_elsewhere"]
    }
    if set(actual_collisions) != set(expected_collisions):
        raise RuntimeError(
            "Same-name accounts have changed since proposal creation. "
            "Refresh the evidence and obtain approval again."
        )
    for aid, old in expected_collisions.items():
        live = actual_collisions[aid]
        if any(str(live.get(k, "")) != str(old[old_key]) for k, old_key in [
            ("name", "name"), ("billing_city", "city"),
            ("billing_state", "state"), ("billing_street", "street")
        ]):
            raise RuntimeError("Existing same-name account changed since review")

    actual_nearby = {
        a["account_id"]: a for a in others
        if a.get("billing_city", "").casefold() == p["website_address"]["city"].casefold()
        and a.get("billing_state", "").casefold() == p["website_address"]["state"].casefold()
    }
    expected_nearby = {a["account_id"]: a for a in p["same_city_accounts"]}
    if set(actual_nearby) != set(expected_nearby):
        raise RuntimeError("Same-city account list changed since review")
    for aid, old in expected_nearby.items():
        live = actual_nearby[aid]
        if (live["name"] != old["name"]
                or live["billing_street"] != old["street"]
                or live.get("parent_name") != old["parent"]):
            raise RuntimeError("Nearby account changed since review")


def _verify(account_id, p, headers):
    account = crm.get_account(account_id, headers)
    if account.get("account_id") != account_id:
        raise RuntimeError("Created-account identity mismatch")
    if account.get("name") != p["proposed_name"]:
        raise RuntimeError("Created-account name mismatch")
    if account.get("parent_id") != crm.BELLHAVEN_PARENT_ID:
        raise RuntimeError("Created account does not belong to Bellhaven")
    if not _same_location(account, p):
        raise RuntimeError("Created-account address mismatch")
    if account.get("care_type") != p["proposed_care_type"]:
        raise RuntimeError("Created-account care type mismatch")
    if account.get("status") != "Active":
        raise RuntimeError("Created account is not active")
    if (account.get("lifetime_revenue") != 0
            or account.get("outstanding_ar") != 0):
        raise RuntimeError("New account unexpectedly contains financial history")
    if _marker(p["proposal_id"]) not in str(account.get("note") or ""):
        raise RuntimeError("New account lacks its proposal identifier")
    if account.get("duplicate_of_account") or account.get("chow_current_account"):
        raise RuntimeError("Unexpected duplicate/CHOW link on new account")
    return account


def apply_create(proposal_id):
    """Create or safely recover an approved missing-account proposal.

    No automatic second POST if the first request's outcome is uncertain.
    """
    lock_path = PROJECT / "create_writer.lock"
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _apply_locked(proposal_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _apply_locked(proposal_id):
    p = _approved(proposal_id)
    headers = crm._headers()
    journal = _journal()
    saved = journal.get(proposal_id)
    fingerprint = _fingerprint(p)
    if saved and saved.get("fingerprint") != fingerprint:
        raise RuntimeError("Proposal changed after a previous CREATE attempt")

    live = crm._all_accounts(headers)
    ids = [a["account_id"] for a in live]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Live CRM returned repeated account IDs")

    discovered_id = _live_candidates(live, p)
    _validate_other_accounts(live, p, discovered_id)

    if saved and saved.get("account_id"):
        if discovered_id != saved["account_id"]:
            raise RuntimeError("CREATE journal disagrees with live CRM")

    if discovered_id:
        _verify(discovered_id, p, headers)
        journal[proposal_id] = {
            "state": "verified", "account_id": discovered_id,
            "fingerprint": fingerprint,
        }
        _save_journal(journal)
        return f"CREATE already exists and is verified: {discovered_id}"

    if saved:
        # The first POST might have worked even if its response was lost.
        raise RuntimeError(
            "Previous CREATE attempt is unresolved. No repeat POST sent. "
            "Inspect the live CRM and journal before proceeding."
        )

    w = p["website_address"]
    payload = {
        "name": p["proposed_name"],
        "parent_id": crm.BELLHAVEN_PARENT_ID,
        "billing_street": w["street"],
        "billing_city": w["city"],
        "billing_state": w["state"],
        "billing_zip": w["zip"],
        "care_type": p["proposed_care_type"],
        "status": "Active",
        "note": f"{_marker(proposal_id)} Evidence: {p['source_url']}",
    }

    # The journal is durable BEFORE POST. An ambiguous network result cannot
    # cause a second account to be created automatically.
    journal[proposal_id] = {
        "state": "create_attempted", "fingerprint": fingerprint,
    }
    _save_journal(journal)

    request = Request(
        f"{crm.BASE_URL}/accounts",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = response.read()

    # Do not trust POST alone; identify the newly created account in a fresh GET.
    posted_id = None
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            posted_id = parsed.get("account_id") or parsed.get("id")
        elif isinstance(parsed, str) and re.fullmatch(r"001[A-Za-z0-9]{15}", parsed):
            posted_id = parsed
    except (ValueError, UnicodeDecodeError):
        pass

    live = crm._all_accounts(headers)
    discovered_id = _live_candidates(live, p)
    if not discovered_id or (posted_id and posted_id != discovered_id):
        raise RuntimeError(
            "POST completed but the new account could not be uniquely identified. "
            "No retry permitted until manually checked."
        )
    _verify(discovered_id, p, headers)
    journal[proposal_id] = {
        "state": "verified", "account_id": discovered_id,
        "fingerprint": fingerprint,
    }
    _save_journal(journal)
    return f"CREATE applied and independently verified: {discovered_id}"
