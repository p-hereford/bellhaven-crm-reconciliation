"""Reviewer-gated, retry-safe Owosso duplicate resolution.

A contact transfer is verified before a duplicate can be deactivated.
The historical accounts remain separate and retain their financial fields.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import crm_writer as crm

PROJECT = Path(__file__).resolve().parent
JOURNAL_NAME = "duplicate_journal.json"
MARKER = "Duplicate of Bellhaven of Owosso canonical account"


def _load(name):
    with open(PROJECT / name, encoding="utf-8") as handle:
        return json.load(handle)


def _save_journal(data):
    target = PROJECT / JOURNAL_NAME
    temp = target.with_suffix(".json.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def _journal():
    path = PROJECT / JOURNAL_NAME
    return _load(JOURNAL_NAME) if path.exists() else {}


def _approved(proposal_id):
    proposals = _load("proposals_duplicate_draft.json")
    matches = [p for p in proposals if p.get("proposal_id") == proposal_id]
    if len(matches) != 1:
        raise ValueError("Duplicate proposal is missing or repeated")
    p = matches[0]
    if p.get("action") != "RESOLVE_DUPLICATE":
        raise ValueError("Wrong proposed action")
    if p.get("proposal_id") != f"duplicate:{p.get('duplicate_account_id')}":
        raise ValueError("Duplicate proposal ID is inconsistent")
    if _load("review_decisions.json").get(proposal_id) != "staged":
        raise ValueError("Human reviewer approval must be STAGED")
    if p.get("canonical_account_id") == p.get("duplicate_account_id"):
        raise ValueError("Canonical and duplicate account IDs must differ")
    preserved = p.get("contact_ids_to_preserve")
    moved = p.get("contact_ids_to_reassign")
    if (not isinstance(preserved, list) or len(preserved) != 2
            or len(set(preserved)) != 2 or not isinstance(moved, list)
            or len(moved) != 1 or moved[0] not in preserved):
        raise ValueError("Contact preservation plan is invalid")
    if p.get("proposed_duplicate_status") != "Inactive":
        raise ValueError("Unexpected duplicate status")
    if p.get("proposed_duplicate_of_account") != p["canonical_account_id"]:
        raise ValueError("Unexpected duplicate target")
    if p.get("facility_name") != "Bellhaven of Owosso":
        raise ValueError("Unexpected facility")
    facilities = [f for f in _load("bellhaven_facilities.json")
                  if f.get("name") == p["facility_name"]
                  and f.get("source_url") == p.get("source_url")]
    if len(facilities) != 1:
        raise ValueError("Original website evidence cannot be uniquely identified")
    return p, facilities[0]


def _fingerprint(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode("utf-8")).hexdigest()


def _contact_page(account_id, headers, page):
    params = urlencode({"account_id": account_id, "page": page, "page_size": 50})
    req = Request(f"{crm.BASE_URL}/contacts?{params}", headers=headers, method="GET")
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def _contacts(account_id, headers):
    results = []
    for page in range(1, 101):
        data = _contact_page(account_id, headers, page)
        batch = data["data"]
        results.extend(batch)
        if len(results) >= data["total"]:
            break
        if not batch:
            raise RuntimeError("CRM contact pagination stopped prematurely")
    else:
        raise RuntimeError("CRM contact pagination exceeded safety limit")
    if len(results) != data["total"]:
        raise RuntimeError("CRM contact list is incomplete")
    ids = [c["contact_id"] for c in results]
    if len(ids) != len(set(ids)):
        raise RuntimeError("CRM returned duplicate contact IDs")
    if any(c.get("account_id") != account_id for c in results):
        raise RuntimeError("CRM contact account filter produced an inconsistent result")
    return {c["contact_id"]: c for c in results}


def _get_contact(contact_id, headers):
    req = Request(f"{crm.BASE_URL}/contacts/{contact_id}", headers=headers, method="GET")
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def _patch_contact(contact_id, account_id, headers):
    req = Request(
        f"{crm.BASE_URL}/contacts/{contact_id}",
        data=json.dumps({"account_id": account_id}).encode("utf-8"),
        headers=headers,
        method="PATCH",
    )
    with urlopen(req, timeout=20) as response:
        response.read()


def _compare(before, after, allowed, description):
    changes = {key for key in set(before) | set(after)
               if before.get(key) != after.get(key)}
    unexpected = changes - set(allowed) - {"updated_at"}
    if unexpected:
        raise RuntimeError(f"Unexpected {description} changes: {sorted(unexpected)}")


def _validate_accounts(canonical, duplicate, website, p, *, permit_finished):
    for a in (canonical, duplicate):
        if a["name"] != website["name"]:
            raise ValueError("Unexpected account name")
        if not crm._same_location(a, website):
            raise ValueError("CRM account location no longer matches evidence")
        if a["parent_id"] != crm.BELLHAVEN_PARENT_ID:
            raise ValueError("Unexpected account parent")
        if a["care_type"] != "Assisted Living":
            raise ValueError("Unexpected care type")
        if (a["lifetime_revenue"] != 0 or a["outstanding_ar"] != 0):
            raise ValueError("Financial history prevents automatic duplicate resolution")
        if a.get("chow_current_account"):
            raise ValueError("CHOW account cannot be deduplicated")
    if canonical["account_id"] != p["canonical_account_id"]:
        raise ValueError("Canonical identity mismatch")
    if canonical["billing_street"] != website["street"]:
        raise ValueError("Canonical street does not exactly match the website")
    if canonical["status"] != "Active" or canonical.get("duplicate_of_account"):
        raise ValueError("Canonical account cannot be reused")
    if duplicate["account_id"] != p["duplicate_account_id"]:
        raise ValueError("Duplicate identity mismatch")
    finished = (duplicate["status"] == "Inactive"
                and duplicate.get("duplicate_of_account") == canonical["account_id"]
                and MARKER in str(duplicate.get("note") or ""))
    if finished and permit_finished:
        return True
    if duplicate["status"] != "Active" or duplicate.get("duplicate_of_account"):
        raise ValueError("Duplicate account is in an unexpected partial state")
    return False


def _read_state(p, website, headers):
    canonical = crm.get_account(p["canonical_account_id"], headers)
    duplicate = crm.get_account(p["duplicate_account_id"], headers)
    completed = _validate_accounts(canonical, duplicate, website, p, permit_finished=True)
    first = _contacts(p["canonical_account_id"], headers)
    second = _contacts(p["duplicate_account_id"], headers)
    expected = set(p["contact_ids_to_preserve"])
    if set(first) & set(second) or set(first) | set(second) != expected:
        raise ValueError("Associated contacts changed; obtain a new review")
    moved_id = p["contact_ids_to_reassign"][0]
    if moved_id not in first and moved_id not in second:
        raise ValueError("Transfer contact cannot be found")
    if completed and second:
        raise RuntimeError("Inactive duplicate still has associated contacts")
    return canonical, duplicate, first, second, completed


def apply_duplicate(proposal_id):
    """Transfer approved contact first, then inactivate duplicate; safe to retry.

    The workflow does not assume PATCH accepted a field: every change is checked
    by a fresh GET. If an API reply is lost, a retry first inspects live state.
    """
    with open(PROJECT / "duplicate_writer.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _apply_locked(proposal_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _apply_locked(proposal_id):
    p, website = _approved(proposal_id)
    headers = crm._headers()
    original_canonical, original_duplicate, first, second, finished = _read_state(
        p, website, headers
    )
    journal = _journal()
    saved = journal.get(proposal_id)
    fp = _fingerprint(p)
    if saved and saved["fingerprint"] != fp:
        raise ValueError("Proposal changed after the first duplicate-resolution attempt")
    if not saved:
        # A previous run may have partially executed only if it had this journal.
        if finished or p["contact_ids_to_reassign"][0] in first:
            raise RuntimeError("Unexpected pre-existing duplicate resolution; investigate")
        saved = {
            "fingerprint": fp,
            "canonical_before": original_canonical,
            "duplicate_before": original_duplicate,
            "contacts_before": {**first, **second},
            "state": "started",
        }
        journal[proposal_id] = saved
        _save_journal(journal)

    canonical_before = saved["canonical_before"]
    duplicate_before = saved["duplicate_before"]
    contacts_before = saved["contacts_before"]
    _compare(canonical_before, original_canonical, {}, "canonical-account")
    _compare(duplicate_before, original_duplicate,
             {"status", "duplicate_of_account", "note"}, "duplicate-account")

    moved_id = p["contact_ids_to_reassign"][0]
    if moved_id in second:
        _patch_contact(moved_id, p["canonical_account_id"], headers)

    canonical, duplicate, first, second, finished = _read_state(p, website, headers)
    if second or set(first) != set(p["contact_ids_to_preserve"]):
        raise RuntimeError("Contact reassignment not verified; duplicate NOT deactivated")
    for contact_id, baseline in contacts_before.items():
        actual = _get_contact(contact_id, headers)
        if actual.get("contact_id") != contact_id:
            raise RuntimeError("Contact identity changed")
        if actual.get("account_id") != p["canonical_account_id"]:
            raise RuntimeError("Contact does not belong to canonical account")
        permitted = {"account_id"} if contact_id == moved_id else set()
        _compare(baseline, actual, permitted, "contact")

    _compare(canonical_before, canonical, {}, "canonical-account")
    _compare(duplicate_before, duplicate,
             {"status", "duplicate_of_account", "note"}, "duplicate-account")

    if not finished:
        # The only direct update to the duplicate account is the metadata of
        # its duplicate status; nothing is deleted or merged.
        note = (
            f"{MARKER} {p['canonical_account_id']}. "
            f"Contact {moved_id} reassigned to canonical account. "
            f"Source: {p['source_url']}"
        )
        crm._patch_account(p["duplicate_account_id"], {
            "status": "Inactive",
            "duplicate_of_account": p["canonical_account_id"],
            "note": note,
        }, headers)

    canonical, duplicate, first, second, finished = _read_state(p, website, headers)
    if not finished or second or set(first) != set(p["contact_ids_to_preserve"]):
        raise RuntimeError("Final duplicate resolution not verified")
    _compare(canonical_before, canonical, {}, "canonical-account")
    _compare(duplicate_before, duplicate,
             {"status", "duplicate_of_account", "note"}, "duplicate-account")
    journal[proposal_id]["state"] = "verified"
    _save_journal(journal)
    return (
        f"Owosso duplicate verified: {p['duplicate_account_id']} marked Inactive "
        f"and linked to {p['canonical_account_id']}; both contacts preserved."
    )
