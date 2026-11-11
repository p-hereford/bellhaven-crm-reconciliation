import json
from pathlib import Path

from crm_writer import (
    BELLHAVEN_PARENT_ID,
    _all_accounts,
    _headers,
    _same_location,
    load_json,
)

PROJECT = Path(__file__).parent

facilities = load_json("bellhaven_facilities.json")

# Fetch fresh account data directly from the live CRM.
accounts = _all_accounts(_headers())

assert len(facilities) == 35, "Unexpected facility count"

urls = [f["source_url"] for f in facilities]
assert len(urls) == len(set(urls)), "Duplicate website URLs"

account_ids = [a["account_id"] for a in accounts]
assert len(account_ids) == len(set(account_ids)), \
    "Duplicate CRM account IDs"

results = []
problems = []

for facility in facilities:

    address = {
        "street": facility["street"],
        "city": facility["city"],
        "state": facility["state"],
        "zip": facility["zip"],
    }

    # Match by physical location, not name alone.
    location_matches = [
        account
        for account in accounts
        if _same_location(account, address)
    ]

    # Only active, current Bellhaven accounts qualify.
    valid_matches = [
        account
        for account in location_matches
        if (
            account["parent_id"] == BELLHAVEN_PARENT_ID
            and account["status"] == "Active"
            and not account.get("duplicate_of_account")
            and not account.get("chow_current_account")
        )
    ]

    exception = None

    # Billing PO Boxes must not be overwritten with
    # physical addresses merely to force a match.
    if not valid_matches and facility["name"] == \
            "Bellhaven of Ashtabula":

        valid_matches = [
            account
            for account in accounts
            if (
                account["name"] == facility["name"]
                and account["billing_city"] == facility["city"]
                and account["billing_state"] == facility["state"]
                and str(account["billing_zip"])
                == str(facility["zip"])
                and account["billing_street"]
                .strip().casefold().startswith("po box")
                and account["parent_id"] == BELLHAVEN_PARENT_ID
                and account["status"] == "Active"
                and not account.get("duplicate_of_account")
            )
        ]

        if valid_matches:
            exception = "Billing PO Box differs from physical address"

    if len(valid_matches) == 1:
        account = valid_matches[0]

        if account["name"] != facility["name"]:
            result = "NAME_MISMATCH"
            problems.append(facility["name"])
        elif exception:
            result = "MATCHED_WITH_BILLING_EXCEPTION"
        else:
            result = "MATCHED"

        selected_id = account["account_id"]

    elif not valid_matches:
        result = "MISSING_ACTIVE_BELLHAVEN_ACCOUNT"
        selected_id = None
        problems.append(facility["name"])

    else:
        result = "MULTIPLE_ACTIVE_BELLHAVEN_ACCOUNTS"
        selected_id = None
        problems.append(facility["name"])

    competing = [
        account["account_id"]
        for account in location_matches
        if account["parent_id"] != BELLHAVEN_PARENT_ID
    ]

    results.append({
        "facility": facility["name"],
        "source_url": facility["source_url"],
        "result": result,
        "selected_account_id": selected_id,
        "valid_match_count": len(valid_matches),
        "competing_account_ids": competing,
        "exception": exception,
    })

    print("\n" + "=" * 55)
    print("FACILITY:", facility["name"])
    print("RESULT:", result)
    print("BELLHAVEN ACCOUNT:", selected_id or "NONE")
    print("COMPETING HISTORICAL ACCOUNTS:", len(competing))

    if exception:
        print("ADDRESS EXCEPTION:", exception)

    if result in {
        "MISSING_ACTIVE_BELLHAVEN_ACCOUNT",
        "MULTIPLE_ACTIVE_BELLHAVEN_ACCOUNTS",
        "NAME_MISMATCH",
    }:
        print("INVESTIGATION REQUIRED")

# Check that a single CRM account was not assigned
# to two different website facilities.
chosen_ids = [
    result["selected_account_id"]
    for result in results
    if result["selected_account_id"]
]

if len(chosen_ids) != len(set(chosen_ids)):
    problems.append("A CRM account was assigned to multiple facilities")

summary = {
    "website_facilities": len(facilities),
    "live_crm_accounts": len(accounts),
    "matched": sum(
        r["result"] == "MATCHED" for r in results
    ),
    "billing_address_exceptions": sum(
        r["result"] == "MATCHED_WITH_BILLING_EXCEPTION"
        for r in results
    ),
    "unresolved": len(problems),
}

output = PROJECT / "full_coverage_audit.json"

output.write_text(
    json.dumps({
        "summary": summary,
        "results": results,
        "problems": problems,
    }, indent=4),
    encoding="utf-8",
)

print("\n" + "=" * 55)
print("FULL RECONCILIATION SUMMARY")
print("Website facilities:", summary["website_facilities"])
print("Live CRM accounts:", summary["live_crm_accounts"])
print("Exact-location matches:", summary["matched"])
print(
    "Billing-address exceptions:",
    summary["billing_address_exceptions"]
)
print("Unresolved facilities:", summary["unresolved"])
print("Audit saved:", output.name)
print("No CRM records modified.")

if problems:
    print("\nFACILITIES REQUIRING INVESTIGATION:")
    for problem in problems:
        print("-", problem)
else:
    print("\nALL 35 FACILITIES ACCOUNTED FOR")