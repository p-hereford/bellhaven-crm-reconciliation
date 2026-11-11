"""Read-only daily Bellhaven website / CRM reconciliation.

Reuses main.py to scrape into an isolated temporary directory, so the
original assessment evidence and CRM baseline are never overwritten.
No CRM PATCH, POST, PUT or DELETE is performed in this module.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from crm_writer import BELLHAVEN_PARENT_ID, _all_accounts, _headers, _same_location

PROJECT = Path(__file__).resolve().parent
REFERENCE_WEBSITE = PROJECT / "bellhaven_facilities.json"
LATEST_REPORT = PROJECT / "latest_daily_report.json"


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path, value):
    """Replace the output only after the whole JSON has been written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".daily_report_", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def scrape_without_overwriting_reference():
    """Run the existing scraper somewhere it cannot overwrite saved evidence."""
    with tempfile.TemporaryDirectory(prefix="bellhaven_scrape_") as temporary:
        outcome = subprocess.run(
            [sys.executable, str(PROJECT / "main.py")],
            cwd=temporary,
            text=True,
            capture_output=True,
            timeout=900,
            check=False,
        )
        if outcome.returncode:
            raise RuntimeError(
                "Website scraper failed; no report replaced.\n"
                + outcome.stderr[-1200:]
            )
        output = Path(temporary) / "bellhaven_facilities.json"
        if not output.exists():
            raise RuntimeError("Scraper completed without a website snapshot")
        return read_json(output)


def validate_website(facilities, reference):
    if not isinstance(facilities, list) or not facilities:
        raise ValueError("Website snapshot is empty or malformed")
    required = ("name", "street", "city", "state", "zip", "source_url", "care_offerings")
    for index, facility in enumerate(facilities):
        if not isinstance(facility, dict):
            raise ValueError(f"Invalid facility at index {index}")
        if any(not facility.get(field) for field in required):
            raise ValueError(f"Incomplete website facility at index {index}")
        if not isinstance(facility["care_offerings"], list):
            raise ValueError(f"Invalid care offerings at index {index}")
    urls = [f["source_url"] for f in facilities]
    if len(urls) != len(set(urls)):
        raise ValueError("Duplicate website URLs")
    # Large unexpected drops are likely scraper/page failures, not genuine closures.
    # Smaller changes remain visible in the report for human investigation.
    threshold = max(1, (len(reference) * 4 + 4) // 5)
    if len(facilities) < threshold:
        raise ValueError(
            f"Website count dropped from {len(reference)} to {len(facilities)}; "
            f"below safety floor {threshold}. No report replaced."
        )


def expected_care_types(offerings):
    result = set()
    for offering in offerings:
        text = str(offering).casefold()
        if "memory" in text:
            result.add("Memory Care")
        if "assisted" in text:
            result.add("Assisted Living")
        if "nursing" in text or "rehabilitation" in text:
            result.add("Skilled Nursing")
    return result


def current_account(account):
    return (
        account.get("parent_id") == BELLHAVEN_PARENT_ID
        and account.get("status") == "Active"
        and not account.get("duplicate_of_account")
        and not account.get("chow_current_account")
    )


def billing_exception_matches(account, facility):
    """Known Ashtabula PO Box versus physical address distinction."""
    if facility["name"] != "Bellhaven of Ashtabula":
        return False
    return (
        current_account(account)
        and account.get("name") == facility["name"]
        and account.get("billing_city") == facility["city"]
        and account.get("billing_state") == facility["state"]
        and str(account.get("billing_zip")) == str(facility["zip"])
        and str(account.get("billing_street", "")).strip().casefold().startswith("po box")
    )


def reconcile(facilities, accounts, reference):
    all_ids = [a["account_id"] for a in accounts]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("CRM returned duplicate account IDs")
    if not accounts:
        raise ValueError("CRM returned no accounts")
    if not any(a.get("account_id") == BELLHAVEN_PARENT_ID for a in accounts):
        raise ValueError("Bellhaven parent account missing from live CRM")

    results = []
    issues = []
    selected_ids = []
    competing = []

    for facility in facilities:
        address = {key: facility[key] for key in ("street", "city", "state", "zip")}
        at_location = [a for a in accounts if _same_location(a, address)]
        valid = [a for a in at_location if current_account(a)]
        address_exception = False
        if not valid:
            valid = [a for a in accounts if billing_exception_matches(a, facility)]
            address_exception = bool(valid)

        row = {
            "facility": facility["name"],
            "source_url": facility["source_url"],
            "matched_account_id": None,
            "status": "UNRESOLVED",
            "issues": [],
        }
        if len(valid) == 1:
            match = valid[0]
            row["matched_account_id"] = match["account_id"]
            selected_ids.append(match["account_id"])
            row["status"] = "MATCHED_WITH_BILLING_EXCEPTION" if address_exception else "MATCHED"
            if match.get("name") != facility["name"]:
                row["issues"].append("NAME_MISMATCH")
            care_types = expected_care_types(facility["care_offerings"])
            if care_types and match.get("care_type") not in care_types:
                row["issues"].append("CARE_TYPE_MISMATCH")
        elif not valid:
            row["issues"].append("MISSING_ACTIVE_BELLHAVEN_ACCOUNT")
        else:
            row["issues"].append("MULTIPLE_ACTIVE_BELLHAVEN_ACCOUNTS")
            row["candidate_account_ids"] = [a["account_id"] for a in valid]

        other_parents = [
            a for a in at_location
            if a.get("parent_id") != BELLHAVEN_PARENT_ID
        ]
        if other_parents:
            competing.append({
                "website_facility": facility["name"],
                "other_parent_account_ids": [a["account_id"] for a in other_parents],
                "already_linked_chow": [
                    a["account_id"] for a in other_parents
                    if a.get("chow_current_account")
                ],
            })
        if row["issues"]:
            issues.append({
                "website_facility": facility["name"],
                "source_url": facility["source_url"],
                "matched_account_id": row["matched_account_id"],
                "issues": row["issues"],
            })
        results.append(row)

    if len(selected_ids) != len(set(selected_ids)):
        issues.append({
            "website_facility": "MULTIPLE_WEBSITE_FACILITIES",
            "issues": ["ONE_CRM_ACCOUNT_MATCHED_MORE_THAN_ONE_FACILITY"],
        })

    selected_set = set(selected_ids)
    absent_bellhaven = [
        {"account_id": a["account_id"], "name": a.get("name")}
        for a in accounts
        if current_account(a) and a["account_id"] not in selected_set
    ]
    prior_urls = {f["source_url"] for f in reference}
    current_urls = {f["source_url"] for f in facilities}
    counts = Counter(row["status"] for row in results)

    summary = {
        "website_facilities": len(facilities),
        "live_crm_accounts": len(accounts),
        "matched": counts["MATCHED"],
        "billing_address_exceptions": counts["MATCHED_WITH_BILLING_EXCEPTION"],
        "facilities_with_issues": sum(bool(row["issues"]) for row in results),
        "new_website_entries": len(current_urls - prior_urls),
        "removed_website_entries": len(prior_urls - current_urls),
        "active_bellhaven_accounts_absent_from_website": len(absent_bellhaven),
        "locations_with_other_parent_accounts": len(competing),
        "crm_writes": 0,
    }
    return {
        "status": "PASS" if not issues else "REVIEW_REQUIRED",
        "summary": summary,
        "facility_results": results,
        "issues_for_review": issues,
        "website_added_urls": sorted(current_urls - prior_urls),
        "website_removed_urls": sorted(prior_urls - current_urls),
        "existing_bellhaven_accounts_absent_from_website": absent_bellhaven,
        "other_parent_accounts_at_current_locations": competing,
    }


def main():
    reference = read_json(REFERENCE_WEBSITE)
    print("Scanning website into an isolated temporary directory...")
    facilities = scrape_without_overwriting_reference()
    validate_website(facilities, reference)

    print("Reading current CRM accounts (GET only)...")
    accounts = _all_accounts(_headers())
    report = reconcile(facilities, accounts, reference)
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["reference_website_facilities"] = len(reference)

    old_report = read_json(LATEST_REPORT) if LATEST_REPORT.exists() else None
    if old_report:
        old = old_report["summary"]
        new = report["summary"]
        report["comparison_to_previous_run"] = {
            "website_facility_count_changed": old["website_facilities"] != new["website_facilities"],
            "live_crm_account_count_changed": old["live_crm_accounts"] != new["live_crm_accounts"],
            "facilities_with_issues_changed": old["facilities_with_issues"] != new["facilities_with_issues"],
        }
    else:
        report["comparison_to_previous_run"] = "FIRST_RUN"

    atomic_json(LATEST_REPORT, report)
    print("\nDAILY MONITOR:", report["status"])
    for key, value in report["summary"].items():
        print(f"{key}: {value}")
    print("Report:", LATEST_REPORT.name)
    print("CRM modifications: NONE")
    print("Reference snapshots and review decisions: UNCHANGED")


if __name__ == "__main__":
    main()
