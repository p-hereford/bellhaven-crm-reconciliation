import fcntl
import json
import os
import re

from pathlib import Path
from urllib.request import Request, urlopen
from dotenv import load_dotenv

PROJECT = Path(__file__).parent
load_dotenv(PROJECT / ".env")

BASE_URL = "https://analyst-assessment-production.up.railway.app/api/v1"
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"


def load_json(filename):
    with open(PROJECT / filename, encoding="utf-8") as file:
        return json.load(file)


def normalise_street(value):
    abbreviations = {
        "lane": "ln", "road": "rd", "street": "st",
        "avenue": "ave", "drive": "dr", "boulevard": "blvd",
        "pike": "pk", "north": "n", "south": "s",
        "east": "e", "west": "w", "northwest": "nw",
    }
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    return " ".join(abbreviations.get(word, word) for word in words)


def get_account(account_id, headers):
    request = Request(
        f"{BASE_URL}/accounts/{account_id}",
        headers=headers,
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def _headers():
    token = os.getenv("CRM_API_TOKEN")
    if not token:
        raise ValueError("Missing CRM API token")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _proposal(proposal_id, action):
    matches = [
        item for item in load_json("proposals.json")
        if item["proposal_id"] == proposal_id
    ]
    if len(matches) != 1:
        raise ValueError("Proposal missing or duplicated")
    proposal = matches[0]
    if proposal["action"] != action:
        raise ValueError("This action requires another procedure")
    decisions = load_json("review_decisions.json")
    if decisions.get(proposal_id) != "staged":
        raise ValueError("Proposal has not been approved")
    return proposal


def _same_location(account, website):
    if normalise_street(account.get("billing_street")) != normalise_street(website["street"]):
        return False
    for crm_key, web_key in [
        ("billing_city", "city"),
        ("billing_state", "state"),
        ("billing_zip", "zip"),
    ]:
        if str(account.get(crm_key, "")).strip().casefold() != str(website[web_key]).strip().casefold():
            return False
    return True


def _patch_account(account_id, changes, headers):
    request = Request(
        f"{BASE_URL}/accounts/{account_id}",
        data=json.dumps(changes).encode("utf-8"),
        headers=headers,
        method="PATCH",
    )
    with urlopen(request, timeout=20) as response:
        response.read()


def apply_reparent(proposal_id):
    proposal = _proposal(proposal_id, "REPARENT")
    headers = _headers()
    account_id = proposal["crm_account_id"]
    account = get_account(account_id, headers)

    if account["account_id"] != account_id:
        raise ValueError("Account identity mismatch")

    proposed_name = proposal["proposed_name"]
    proposed_parent = proposal["proposed_parent_id"]
    if proposed_parent != BELLHAVEN_PARENT_ID:
        raise ValueError("Unexpected target parent")
    if account["name"] not in {proposal["crm_name"], proposed_name}:
        raise ValueError("Account name changed since review")
    if account["parent_id"] not in {proposal["current_parent_id"], proposed_parent}:
        raise ValueError("Account parent changed since review")
    if account["status"] != "Active":
        raise ValueError("Account is no longer active")
    if account.get("duplicate_of_account"):
        raise ValueError("Account is marked as a duplicate")
    if account.get("chow_current_account"):
        raise ValueError("Account already has a CHOW successor")
    if not _same_location(account, proposal["website_address"]):
        raise ValueError("Location does not match website evidence")
    if (account["lifetime_revenue"] != proposal["revenue"]
            or account["outstanding_ar"] != proposal["outstanding_ar"]):
        raise ValueError("Financial data changed. Review again.")
    if account["lifetime_revenue"] > 0 and account["outstanding_ar"] > 0:
        raise ValueError("CHOW required. Re-parent blocked.")

    changes = {}
    if account["parent_id"] != proposed_parent:
        changes["parent_id"] = proposed_parent
    if account["name"] != proposed_name:
        changes["name"] = proposed_name
    if changes:
        _patch_account(account_id, changes, headers)

    updated = get_account(account_id, headers)
    if updated["parent_id"] != proposed_parent:
        raise RuntimeError("Parent correction not verified")
    if updated["name"] != proposed_name:
        raise RuntimeError("Name correction not verified")
    for key in ("lifetime_revenue", "outstanding_ar", "status"):
        if updated[key] != account[key]:
            raise RuntimeError(f"Unexpected change to {key}")
    return f"Verified CRM correction: {updated['name']}"


def _all_accounts(headers):
    accounts = []
    for page in range(1, 101):
        request = Request(
            f"{BASE_URL}/accounts?page={page}&page_size=50",
            headers=headers,
            method="GET",
        )
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        batch = result["data"]
        accounts.extend(batch)
        if len(accounts) >= result["total"]:
            return accounts
        if not batch:
            raise RuntimeError("CRM pagination ended unexpectedly")
    raise RuntimeError("CRM pagination safety limit exceeded")


def _find_successor(old_id, proposal, headers):
    """Return one uniquely identified successor or None; never guess."""
    marker = f"CHOW successor of account {old_id}."
    candidates = []
    for item in _all_accounts(headers):
        if item["account_id"] == old_id:
            continue
        if item.get("parent_id") != BELLHAVEN_PARENT_ID:
            continue
        has_marker = marker in str(item.get("note") or "")
        same_address = _same_location(item, proposal["website_address"])
        if has_marker or same_address:
            candidates.append((item, has_marker))

    if len(candidates) > 1:
        raise RuntimeError("Multiple possible CHOW successors. Manual review required.")
    if not candidates:
        return None
    item, has_marker = candidates[0]
    if not has_marker:
        raise RuntimeError(
            "Bellhaven account already exists at this address, but lacks "
            "the CHOW predecessor marker. Manual review required."
        )
    if item.get("name") != proposal["proposed_name"]:
        raise RuntimeError("Marked successor has an unexpected name")
    if not _same_location(item, proposal["website_address"]):
        raise RuntimeError("Marked successor address disagrees with evidence")
    return item["account_id"]


def _read_journal():
    path = PROJECT / "chow_journal.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def _write_journal(journal):
    path = PROJECT / "chow_journal.json"
    temporary = PROJECT / "chow_journal.json.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(journal, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _verify_successor(successor_id, old_id, proposal, headers):
    successor = get_account(successor_id, headers)
    marker = f"CHOW successor of account {old_id}."
    if successor["account_id"] != successor_id:
        raise RuntimeError("Successor ID mismatch")
    if successor["parent_id"] != BELLHAVEN_PARENT_ID:
        raise RuntimeError("Successor parent is not Bellhaven")
    if successor["name"] != proposal["proposed_name"]:
        raise RuntimeError("Successor name mismatch")
    if not _same_location(successor, proposal["website_address"]):
        raise RuntimeError("Successor address mismatch")
    if marker not in str(successor.get("note") or ""):
        raise RuntimeError("Successor predecessor marker missing")
    if successor["lifetime_revenue"] != 0 or successor["outstanding_ar"] != 0:
        raise RuntimeError("Successor has non-zero financial history")
    if successor["status"] != "Active":
        raise RuntimeError("Successor is not active")
    return successor


def _verify_original_preserved(before, after, successor_id):
    if after["account_id"] != before["account_id"]:
        raise RuntimeError("Original account ID changed")
    if after.get("chow_current_account") != successor_id:
        raise RuntimeError("Original account CHOW link not verified")
    preserved_fields = (
        "name", "parent_id", "parent_name", "billing_street",
        "billing_city", "billing_state", "billing_zip", "care_type",
        "lifetime_revenue", "outstanding_ar", "status",
        "duplicate_of_account", "note",
    )
    for key in preserved_fields:
        if after.get(key) != before.get(key):
            raise RuntimeError(f"Original account field changed unexpectedly: {key}")


def apply_chow(proposal_id):
    """Approval-gated, resumable change-of-ownership procedure."""
    # Serialise attempts on this machine so two Streamlit sessions cannot POST twice.
    lock_path = PROJECT / "chow_writer.lock"
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _apply_chow_locked(proposal_id)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _apply_chow_locked(proposal_id):
    proposal = _proposal(proposal_id, "CHOW")
    headers = _headers()
    old_id = proposal["crm_account_id"]
    original = get_account(old_id, headers)

    if original["account_id"] != old_id:
        raise ValueError("Original account identity mismatch")
    if original["parent_id"] != proposal["current_parent_id"]:
        raise ValueError("Original parent changed. Review required.")
    if original["name"] != proposal["crm_name"]:
        raise ValueError("Original name changed. Review required.")
    if original["status"] != "Active" or original.get("duplicate_of_account"):
        raise ValueError("Original is inactive or a duplicate")
    if not _same_location(original, proposal["website_address"]):
        raise ValueError("Original address does not match website evidence")
    if (original["lifetime_revenue"] != proposal["revenue"]
            or original["outstanding_ar"] != proposal["outstanding_ar"]):
        raise ValueError("Original financials changed. Review required.")
    if not (original["lifetime_revenue"] > 0 and original["outstanding_ar"] > 0):
        raise ValueError("CHOW rule not satisfied")
    if proposal["proposed_parent_id"] != BELLHAVEN_PARENT_ID:
        raise ValueError("Wrong proposed successor parent")

    journal = _read_journal()
    operation = journal.get(proposal_id)
    successor_id = original.get("chow_current_account") or None

    if successor_id:
        _verify_successor(successor_id, old_id, proposal, headers)
        _verify_original_preserved(original, get_account(old_id, headers), successor_id)
        journal[proposal_id] = {"state": "linked", "successor_id": successor_id}
        _write_journal(journal)
        return f"CHOW already linked and verified: {old_id} -> {successor_id}"

    if operation and operation.get("state") in ("created", "linked"):
        successor_id = operation.get("successor_id")
        if not successor_id:
            raise RuntimeError("CHOW journal missing successor ID")
    else:
        successor_id = _find_successor(old_id, proposal, headers)
        if not successor_id:
            if operation:
                # The previous POST may have succeeded despite a timeout.
                # Never issue a second POST automatically in this situation.
                raise RuntimeError(
                    "Previous CHOW create attempt is unresolved. "
                    "Inspect the live CRM before retrying."
                )

            marker = f"CHOW successor of account {old_id}."
            website = proposal["website_address"]
            payload = {
                "name": proposal["proposed_name"],
                "parent_id": BELLHAVEN_PARENT_ID,
                "billing_street": website["street"],
                "billing_city": website["city"],
                "billing_state": website["state"],
                "billing_zip": website["zip"],
                "care_type": original["care_type"],
                "status": "Active",
                "note": f"{marker} Ownership evidence: {proposal['source_url']}",
            }

            # Write-ahead record: if POST times out, a rerun checks the CRM
            # rather than risking a duplicate replacement account.
            journal[proposal_id] = {"state": "create_attempted", "old_id": old_id}
            _write_journal(journal)

            request = Request(
                f"{BASE_URL}/accounts",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urlopen(request, timeout=20) as response:
                response_body = response.read()

            # API docs omit POST response schema. Prefer an actual account ID
            # if returned; otherwise discover the unique account by its marker.
            created_id = None
            try:
                body = json.loads(response_body)
            except (ValueError, UnicodeDecodeError):
                body = None
            if isinstance(body, dict):
                created_id = body.get("account_id") or body.get("id")
            if created_id:
                successor_id = created_id
            else:
                successor_id = _find_successor(old_id, proposal, headers)
            if not successor_id:
                raise RuntimeError(
                    "POST completed but the created account could not be identified. "
                    "Inspect live CRM before any retry."
                )

    _verify_successor(successor_id, old_id, proposal, headers)
    journal[proposal_id] = {"state": "created", "successor_id": successor_id}
    _write_journal(journal)

    # Only the CHOW link changes on the historical account.
    _patch_account(old_id, {"chow_current_account": successor_id}, headers)
    after = get_account(old_id, headers)
    _verify_original_preserved(original, after, successor_id)
    _verify_successor(successor_id, old_id, proposal, headers)

    journal[proposal_id] = {"state": "linked", "successor_id": successor_id}
    _write_journal(journal)
    return f"CHOW applied and verified: {old_id} -> {successor_id}"


def apply_findlay():
    return apply_reparent("ownership:001UKEFGADQ8YCZ4YM")
