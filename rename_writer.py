import json
from urllib.request import Request, urlopen

from crm_writer import (
    BASE_URL,
    BELLHAVEN_PARENT_ID,
    _all_accounts,
    _headers,
    _same_location,
    get_account,
)

PROTECTED_FIELDS = (
    "account_id",
    "parent_id",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "care_type",
    "status",
    "lifetime_revenue",
    "outstanding_ar",
    "duplicate_of_account",
    "chow_current_account",
    "note",
    "phone",
)


def preflight_rename(proposal):
    """Validate a rename against fresh CRM data without writing."""

    if proposal.get("action") != "RENAME":
        raise ValueError("Only RENAME actions are permitted")

    account_id = proposal["crm_account_id"]
    desired_name = proposal["proposed_name"]
    original_name = proposal["crm_name"]

    if not desired_name or not desired_name.strip():
        raise ValueError("Proposed name is empty")

    headers = _headers()
    account = get_account(account_id, headers)

    if account["parent_id"] != BELLHAVEN_PARENT_ID:
        raise ValueError("Account no longer belongs to Bellhaven")

    if account["status"] != "Active":
        raise ValueError("Account is not active")

    if (
        account.get("duplicate_of_account")
        or account.get("chow_current_account")
    ):
        raise ValueError("Historical or duplicate account cannot be renamed")

    if not _same_location(
        account, proposal["website_address"]
    ):
        raise ValueError("Physical location no longer matches")

    if (
        account["lifetime_revenue"] != proposal["revenue"]
        or account["outstanding_ar"] != proposal["outstanding_ar"]
    ):
        raise ValueError("Financial values changed since proposal preparation")

    current_name = account["name"]

    if current_name == desired_name:
        state = "ALREADY_CORRECT"
    elif current_name == original_name:
        state = "READY_FOR_REVIEW"
    else:
        raise ValueError(
            f"Unexpected live name for {account_id}: {current_name}"
        )

    # Reject any other account with the requested name.
    collisions = [
        other for other in _all_accounts(headers)
        if (
            other["account_id"] != account_id
            and other["name"].strip().casefold()
            == desired_name.strip().casefold()
        )
    ]

    if collisions:
        raise ValueError(
            f"Name collision: {[a['account_id'] for a in collisions]}"
        )

    return {
        "state": state,
        "account_id": account_id,
        "current_name": current_name,
        "proposed_name": desired_name,
        "account_before": account,
    }


def apply_rename(proposal, *, approved=False):
    """
    Apply a name-only correction after explicit reviewer approval.

    No writes occur unless approved=True.
    Repeated calls are safe when the desired name is already present.
    """

    if not approved:
        raise PermissionError("Explicit reviewer approval is required")

    check = preflight_rename(proposal)

    if check["state"] == "ALREADY_CORRECT":
        return {
            "status": "already_applied",
            "account_id": check["account_id"],
            "name": check["proposed_name"],
        }

    account_id = check["account_id"]
    before = check["account_before"]

    headers = {
        **_headers(),
        "Content-Type": "application/json",
    }

    # Deliberately send ONLY the name field.
    payload = json.dumps({
        "name": check["proposed_name"]
    }).encode("utf-8")

    request = Request(
        f"{BASE_URL}/accounts/{account_id}",
        headers=headers,
        data=payload,
        method="PATCH",
    )

    with urlopen(request, timeout=30):
        pass

    after = get_account(account_id, _headers())

    if after["name"] != check["proposed_name"]:
        raise RuntimeError(
            "PATCH completed but the expected name was not verified"
        )

    unexpected = [
        field
        for field in PROTECTED_FIELDS
        if before.get(field) != after.get(field)
    ]

    if unexpected:
        raise RuntimeError(
            "Unexpected protected-field changes: "
            + ", ".join(unexpected)
        )

    return {
        "status": "applied",
        "account_id": account_id,
        "previous_name": before["name"],
        "name": after["name"],
        "protected_fields_verified": True,
    }


if __name__ == "__main__":
    with open(
        "proposals_rename_draft.json", encoding="utf-8"
    ) as file:
        proposals = json.load(file)

    assert len(proposals) == 7

    for proposal in proposals:
        result = preflight_rename(proposal)

        print(
            result["state"],
            "|",
            result["current_name"],
            "->",
            result["proposed_name"],
        )

    print("\nSEVEN RENAME PREFLIGHT CHECKS PASSED")
    print("No CRM records modified.")