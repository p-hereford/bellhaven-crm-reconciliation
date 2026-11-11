"""Bellhaven CRM reviewer interface.

Reads saved, evidence-backed proposals and shows the LIVE CRM state.
A staged human decision is required before a write. Completed proposals remain
visible for auditing, but are never automatically re-applied.
"""

import json
import os
from pathlib import Path

import streamlit as st

from create_writer import apply_create
from zip_writer import apply_zip
from duplicate_writer import apply_duplicate, _contacts as live_contacts
from rename_writer import apply_rename, preflight_rename
from crm_writer import BELLHAVEN_PARENT_ID, _all_accounts, _headers, _same_location, apply_chow, apply_reparent, get_account

PROJECT = Path(__file__).resolve().parent
DECISIONS_PATH = PROJECT / "review_decisions.json"
REVIEW_ONLY_ACTIONS = {"REVIEW_HISTORICAL_OWNERSHIP", "REVIEW_WEBSITE_ABSENCE"}


def read_json(filename, default=None):
    path = PROJECT / filename
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Required file is missing: {filename}")
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_decisions(decisions):
    """Write decisions atomically, keeping the proposals unchanged."""
    temporary = PROJECT / "review_decisions.json.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(decisions, file, indent=4, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, DECISIONS_PATH)


def name_for(proposal, decisions):
    """Every selector label identifies the precise proposal, even on duplicates."""
    status = decisions.get(proposal["proposal_id"], "pending").upper()
    return (
        f"{proposal['facility_name']} | {proposal['action']} "
        f"[{status}] | {proposal['proposal_id']}"
    )


def apply_approved_rename(proposal_id):
    """Require persisted human approval and revalidate saved website evidence."""
    current_decisions = read_json("review_decisions.json", default={})
    if current_decisions.get(proposal_id) != "staged":
        raise PermissionError("Rename must have a persisted staged approval")

    candidates = [
        p for p in read_json("proposals_rename_draft.json")
        if p.get("proposal_id") == proposal_id
    ]
    if len(candidates) != 1:
        raise ValueError("Rename proposal missing or duplicated")
    candidate = candidates[0]
    if candidate.get("action") != "RENAME":
        raise ValueError("Incorrect action for rename writer")
    if proposal_id != f"rename:{candidate.get('crm_account_id')}":
        raise ValueError("Rename proposal ID and CRM account ID disagree")

    website = [
        facility for facility in read_json("bellhaven_facilities.json")
        if facility.get("source_url") == candidate.get("source_url")
        and facility.get("name") == candidate.get("facility_name")
    ]
    if len(website) != 1:
        raise ValueError("Website name and URL do not identify one facility")
    if candidate.get("proposed_name") != website[0]["name"]:
        raise ValueError("Proposed name does not match website evidence")
    expected_address = {
        key: website[0][key] for key in ("street", "city", "state", "zip")
    }
    if candidate.get("website_address") != expected_address:
        raise ValueError("Proposed address differs from saved website evidence")

    result = apply_rename(candidate, approved=True)
    if result.get("status") not in {"applied", "already_applied"}:
        raise RuntimeError("Rename writer did not confirm completion")
    return (
        f"Name confirmed for {result['account_id']}: {result['name']}. "
        f"Writer result: {result['status']}."
    )


st.set_page_config(page_title="Bellhaven CRM Review", layout="wide")
st.title("Bellhaven CRM Review")
st.caption("Evidence-based reconciliation with explicit human approval")

main_proposals = read_json("proposals.json")
create_proposals = read_json("proposals_missing_draft.json")
address_proposals = read_json("proposals_address_draft.json")
duplicate_proposals = read_json("proposals_duplicate_draft.json")
remaining_reviews = read_json("proposals_remaining_review_draft.json")
rename_proposals = read_json("proposals_rename_draft.json")
proposals = (main_proposals + create_proposals + address_proposals
             + duplicate_proposals + remaining_reviews + rename_proposals)

ids = [p["proposal_id"] for p in proposals]
if len(ids) != len(set(ids)):
    st.error("Duplicate proposal identifiers. Review disabled.")
    st.stop()

supported_actions = ({"REPARENT", "CHOW", "CREATE", "FIX_ZIP",
                      "RESOLVE_DUPLICATE", "REVIEW_NAME_COLLISION", "RENAME"}
                     | REVIEW_ONLY_ACTIONS)
if any(p.get("action") not in supported_actions for p in proposals):
    st.error("An unrecognised proposal action was found. Review disabled.")
    st.stop()

decisions = read_json("review_decisions.json", default={})
valid_statuses = {"pending", "staged", "applied", "rejected", "needs_review"}
if any(value not in valid_statuses for value in decisions.values()):
    st.error("An invalid reviewer decision was found. Review disabled.")
    st.stop()

pending = sum(decisions.get(p["proposal_id"], "pending") == "pending" for p in proposals)
staged = sum(decisions.get(p["proposal_id"], "pending") == "staged" for p in proposals)
applied = sum(decisions.get(p["proposal_id"], "pending") == "applied" for p in proposals)
rejected = sum(decisions.get(p["proposal_id"], "pending") == "rejected" for p in proposals)
needs_review = sum(decisions.get(p["proposal_id"], "pending") == "needs_review" for p in proposals)

one, two, three, four, five = st.columns(5)
one.metric("Awaiting review", pending)
two.metric("Approved, not applied", staged)
three.metric("Applied", applied)
four.metric("Rejected", rejected)
five.metric("Needs Review / Hold", needs_review)

st.caption("Completed decisions stay in the audit trail and are not submitted again.")

if not proposals:
    st.info("No proposed changes to review.")
    st.stop()

by_id = {p["proposal_id"]: p for p in proposals}
selection = st.selectbox(
    "Select a proposed correction",
    ids,
    format_func=lambda proposal_id: name_for(by_id[proposal_id], decisions),
    key="selected_proposal_id",
)
proposal = by_id[selection]
proposal_id = proposal["proposal_id"]
action = proposal["action"]
status = decisions.get(proposal_id, "pending")

st.subheader(proposal["facility_name"])
st.write("**Proposed action:**", action)
st.write("**Website evidence:**")
if action in REVIEW_ONLY_ACTIONS:
    # A website-absence review has no facility page: never invent a source URL.
    if action == "REVIEW_HISTORICAL_OWNERSHIP":
        site_matches = [f for f in read_json("bellhaven_facilities.json")
                        if f.get("source_url") == proposal["source_url"]
                        and f.get("name") == proposal["facility_name"]]
        if len(site_matches) != 1:
            st.error("Historical ownership website evidence changed or missing.")
            st.stop()
        f = site_matches[0]
        st.json({k: f[k] for k in ("name", "street", "city", "state",
                                    "zip", "care_offerings", "source_url")})
        st.link_button("Open original facility webpage", f["source_url"])
    else:
        st.json({
            "finding": "No corresponding entry in the saved facility directory",
            "website_facilities_checked": proposal["website_facilities_checked"],
            "evidence_limit": "Directory absence alone does not prove closure",
        })
elif action == "RENAME":
    evidence = [
        facility for facility in read_json("bellhaven_facilities.json")
        if facility.get("source_url") == proposal.get("source_url")
        and facility.get("name") == proposal.get("facility_name")
    ]
    if len(evidence) != 1:
        st.error("Rename website evidence is missing or ambiguous. Review disabled.")
        st.stop()
    expected_address = {
        field: evidence[0][field] for field in ("street", "city", "state", "zip")
    }
    if (proposal.get("website_address") != expected_address
            or proposal.get("proposed_name") != evidence[0]["name"]
            or proposal.get("proposal_id") !=
            f"rename:{proposal.get('crm_account_id')}"):
        st.error("Rename proposal differs from saved website evidence.")
        st.stop()
    if proposal.get("category") not in {
        "EDITORIAL_VARIANT", "POSSIBLE_FORMER_NAME"
    }:
        st.error("Unrecognised rename evidence category. Review disabled.")
        st.stop()
    st.json({field: evidence[0][field] for field in (
        "name", "street", "city", "state", "zip", "care_offerings", "source_url"
    )})
elif action == "RESOLVE_DUPLICATE":
    evidence = [
        f for f in read_json("bellhaven_facilities.json")
        if f.get("source_url") == proposal["source_url"]
        and f.get("name") == proposal["facility_name"]
    ]
    if len(evidence) != 1:
        st.error("Owosso website evidence missing or ambiguous. Review disabled.")
        st.stop()
    st.json({field: evidence[0][field] for field in (
        "street", "city", "state", "zip", "care_offerings", "source_url"
    )})
else:
    st.json(proposal["website_address"])
if action not in REVIEW_ONLY_ACTIONS:
    st.link_button("Open original facility webpage", proposal["source_url"])

if action == "RESOLVE_DUPLICATE":
    st.write("**Proposed canonical (retained) account:**", proposal["canonical_account_id"])
    st.write("**Proposed duplicate (to be inactivated):**", proposal["duplicate_account_id"])
    st.warning(
        "Preserve both existing CRM accounts and both contact records. "
        "Reassign the duplicate account's contact first; independently verify "
        "both contacts on the canonical account before marking the duplicate inactive. "
        "No accounts or contacts will be deleted."
    )
    st.write("**Proposed duplicate status:**", proposal["proposed_duplicate_status"])
    st.write("**Proposed duplicate_of_account:**", proposal["proposed_duplicate_of_account"])
    try:
        headers = _headers()
        canonical = get_account(proposal["canonical_account_id"], headers)
        duplicate = get_account(proposal["duplicate_account_id"], headers)
        canonical_contacts = live_contacts(proposal["canonical_account_id"], headers)
        duplicate_contacts = live_contacts(proposal["duplicate_account_id"], headers)
    except Exception as error:
        st.error(f"Could not inspect live Owosso accounts and contacts: {error}")
        st.stop()
    if (canonical.get("account_id") != proposal["canonical_account_id"]
            or duplicate.get("account_id") != proposal["duplicate_account_id"]):
        st.error("Live Owosso account identity mismatch. Review stopped.")
        st.stop()
    st.write("**Canonical CRM account (LIVE, read-only):**")
    st.json({field: canonical.get(field) for field in (
        "account_id", "name", "billing_street", "billing_city",
        "billing_state", "billing_zip", "parent_id", "care_type",
        "status", "lifetime_revenue", "outstanding_ar",
        "duplicate_of_account", "chow_current_account", "phone", "note",
    )})
    st.write("**Duplicate candidate (LIVE, read-only):**")
    st.json({field: duplicate.get(field) for field in (
        "account_id", "name", "billing_street", "billing_city",
        "billing_state", "billing_zip", "parent_id", "care_type",
        "status", "lifetime_revenue", "outstanding_ar",
        "duplicate_of_account", "chow_current_account", "phone", "note",
    )})
    st.write("**Contact records (LIVE, read-only):**")
    st.json({
        "canonical_account_contacts": [
            {key: contact.get(key) for key in (
                "contact_id", "account_id", "name", "title", "is_active",
            )} for contact in canonical_contacts.values()
        ],
        "duplicate_account_contacts": [
            {key: contact.get(key) for key in (
                "contact_id", "account_id", "name", "title", "is_active",
            )} for contact in duplicate_contacts.values()
        ],
    })
    st.caption(
        "Different contact names and telephone numbers are preserved. "
        "The writer rechecks both accounts and contacts before and after each update."
    )

elif action in REVIEW_ONLY_ACTIONS:
    # Explicit non-write review queue. A HOLD is a persisted reviewer decision,
    # NOT a CRM status update and NOT permission to deactivate an account.
    if proposal.get("recommended_disposition") != "HOLD_FOR_REVIEW":
        st.error("Unexpected review disposition. Review disabled.")
        st.stop()
    st.write("**Recommended disposition:** Needs Review / Hold")
    st.info("Review only. No CRM account or contact will be modified by this decision.")
    st.write("**Associated contacts when proposal was prepared:**",
             proposal["contact_count"])
    st.write("**Lifetime revenue at review:**", proposal["revenue"])
    st.write("**Outstanding AR at review:**", proposal["outstanding_ar"])
    try:
        headers = _headers()
        live_account = get_account(proposal["crm_account_id"], headers)
    except Exception as error:
        st.error(f"Could not read live CRM record: {error}")
        st.stop()
    if live_account.get("account_id") != proposal["crm_account_id"]:
        st.error("Live account identity mismatch. Review disabled.")
        st.stop()
    st.write("**Account requiring investigation (LIVE, read-only):**")
    st.json({key: live_account.get(key) for key in (
        "account_id", "name", "parent_name", "parent_id", "billing_street",
        "billing_city", "billing_state", "billing_zip", "care_type",
        "status", "lifetime_revenue", "outstanding_ar",
        "duplicate_of_account", "chow_current_account", "note",
    )})
    if (live_account.get("lifetime_revenue") != proposal["revenue"]
            or live_account.get("outstanding_ar") != proposal["outstanding_ar"]):
        st.warning("Financial balances have changed since proposal preparation; "
                   "new evidence is required before any write proposal.")
    if action == "REVIEW_HISTORICAL_OWNERSHIP":
        try:
            bellhaven = get_account(proposal["bellhaven_account_id"], headers)
        except Exception as error:
            st.error(f"Could not read current Bellhaven account: {error}")
            st.stop()
        if bellhaven.get("account_id") != proposal["bellhaven_account_id"]:
            st.error("Canonical account identity mismatch. Review disabled.")
            st.stop()
        st.write("**Existing Bellhaven account at this address (LIVE, read-only):**")
        st.json({key: bellhaven.get(key) for key in (
            "account_id", "name", "parent_name", "parent_id", "status",
            "billing_street", "billing_city", "billing_state", "billing_zip",
        )})
        if bellhaven.get("parent_id") != BELLHAVEN_PARENT_ID:
            st.warning("The canonical account's parent has changed since review.")
        st.caption("Competing parent accounts may reflect previous ownership. "
                   "Their historical status is not established by a shared address.")
    else:
        site = read_json("bellhaven_facilities.json")
        current_matches = [f for f in site if (
            f["name"].casefold() == live_account["name"].casefold()
            or _same_location(live_account, {
                "street": f["street"], "city": f["city"],
                "state": f["state"], "zip": f["zip"],
            })
        )]
        st.write("**Website snapshot entries checked:**", len(site))
        st.write("**Current entries matching this account by name or address:**",
                 len(current_matches))
        if len(site) != proposal["website_facilities_checked"] or current_matches:
            st.warning("Website snapshot differs from the prepared review. "
                       "Re-investigation is needed.")
        if proposal.get("financial_history_present"):
            st.warning("This account has financial history or receivables. "
                       "Preserve it pending independent evidence of status.")
        else:
            st.caption("Zero balances and missing directory listing do not "
                       "establish that a facility has closed.")

elif action == "RENAME":
    st.write("**Evidence category:**", proposal["category"])
    st.write("**Existing CRM name:**", proposal["crm_name"])
    st.write("**Proposed website name:**", proposal["proposed_name"])
    st.write("**Lifetime revenue at proposal:**", proposal["revenue"])
    st.write("**Outstanding AR at proposal:**", proposal["outstanding_ar"])
    if proposal["category"] == "POSSIBLE_FORMER_NAME":
        st.warning(
            "The old and current names are materially different. A shared address "
            "and correct parent support the identity match but do not establish "
            "when or why the name changed. If unsure, record Needs Review / Hold "
            "rather than approve a rename."
        )
    else:
        st.info("Editorial naming variation: review the exact current website name.")
    try:
        headers = _headers()
        if status in {"pending", "staged"}:
            check = preflight_rename(proposal)
            live_account = check["account_before"]
        else:
            check = None
            live_account = get_account(proposal["crm_account_id"], headers)
        if live_account.get("account_id") != proposal["crm_account_id"]:
            raise ValueError("CRM account identity mismatch")
        related_contacts = live_contacts(proposal["crm_account_id"], headers)
    except Exception as error:
        st.error(f"Rename CRM preflight or evidence inspection failed: {error}")
        st.stop()

    st.write("**Current LIVE CRM account (read-only):**")
    st.json({field: live_account.get(field) for field in (
        "account_id", "name", "parent_id", "parent_name", "billing_street",
        "billing_city", "billing_state", "billing_zip", "care_type", "status",
        "lifetime_revenue", "outstanding_ar", "duplicate_of_account",
        "chow_current_account", "phone", "note"
    )})
    st.write("**Associated CRM contacts (count only):**", len(related_contacts))
    st.caption("No contact or parent updates are proposed. Only the name may change.")
    if check is not None:
        st.write("**Live safety preflight:**", check["state"])
        if status == "pending" and check["state"] == "ALREADY_CORRECT":
            st.warning(
                "The CRM already has the proposed name without an applied review "
                "decision. Investigate this out-of-band change before proceeding."
            )
            st.stop()

elif action == "CREATE":
    # A CREATE proposal has no original account. Never make up one.
    st.write("**Proposed new CRM account:**")
    st.json({
        "name": proposal["proposed_name"],
        "parent_id": proposal["proposed_parent_id"],
        "care_type": proposal["proposed_care_type"],
        "physical_address": proposal["website_address"],
        "website_care_offerings": proposal["care_offerings"],
    })
    st.warning(
        "A new CRM account will be created only after approval. "
        "Existing accounts must remain unchanged."
    )
    collisions = proposal.get("same_name_elsewhere", [])
    nearby = proposal.get("same_city_accounts", [])
    if collisions:
        st.write("**Different-location accounts with the same name:**")
        st.json(collisions)
        st.warning("Matching names alone do not establish identical facilities.")
    if nearby:
        st.write("**Other CRM accounts in the same city:**")
        st.json(nearby)

    # Read the CRM afresh before showing any approval controls.
    # Writer repeats the check immediately before POST.
    try:
        live_accounts = _all_accounts(_headers())
    except Exception as error:
        st.error(f"Live CRM inspection failed: {error}")
        st.stop()
    live_same_address = [
        a for a in live_accounts if _same_location(a, proposal["website_address"])
    ]
    if live_same_address:
        st.write("**LIVE CRM account(s) at the proposed address:**")
        st.json([{
            "account_id": a["account_id"],
            "name": a.get("name"),
            "parent_id": a.get("parent_id"),
            "status": a.get("status"),
            "note": a.get("note"),
        } for a in live_same_address])
        if status != "applied":
            st.warning(
                "An account now exists at this address. The CREATE writer "
                "will only recover an account bearing its unique creation marker; "
                "otherwise it blocks the operation."
            )
    else:
        st.info("Live CRM: no account currently matches this full physical address.")

else:
    st.write("**Originally matched CRM account:**", proposal["crm_name"])
    if (proposal.get("proposed_name") is not None
            and proposal["proposed_name"] != proposal["crm_name"]):
        st.write("**Proposed new name:**", proposal["proposed_name"])
    if proposal.get("current_parent") is not None:
        st.write("**Parent at time of review:**", proposal["current_parent"] or "None")
    if action == "FIX_ZIP":
        st.write("**Current billing ZIP at review:**", proposal["current_zip"])
        st.write("**Proposed billing ZIP:**", proposal["proposed_zip"])
        st.warning("The website lists the physical ZIP. Confirm it should also be the CRM billing ZIP before approving.")
    st.write("**Lifetime revenue at review:**", proposal["revenue"])
    st.write("**Outstanding AR at review:**", proposal["outstanding_ar"])
    try:
        live_account = get_account(proposal["crm_account_id"], _headers())
    except Exception as error:
        st.error(f"Could not read live CRM account: {error}")
        st.stop()
    if live_account.get("account_id") != proposal["crm_account_id"]:
        st.error("Live CRM account identity mismatch. Review stopped.")
        st.stop()
    st.write("**Current LIVE CRM record (read-only):**")
    st.json({
        "account_id": live_account["account_id"],
        "name": live_account.get("name"),
        "street": live_account.get("billing_street"),
        "city": live_account.get("billing_city"),
        "state": live_account.get("billing_state"),
        "zip": live_account.get("billing_zip"),
        "parent": live_account.get("parent_name"),
        "parent_id": live_account.get("parent_id"),
        "lifetime_revenue": live_account.get("lifetime_revenue"),
        "outstanding_ar": live_account.get("outstanding_ar"),
        "chow_current_account": live_account.get("chow_current_account"),
        "duplicate_of_account": live_account.get("duplicate_of_account"),
        "status": live_account.get("status"),
    })
    if action == "CHOW":
        st.info(
            "CHOW: the original account must retain its parent and financial "
            "history. The new Bellhaven account is linked via chow_current_account."
        )

st.subheader("Reason for proposed correction")
st.write(proposal["explanation"])

st.divider()
st.subheader("Reviewer decision")
st.write("Current status:", status.upper())

if action in REVIEW_ONLY_ACTIONS:
    # A review-only finding cannot ever reach the CRM write callback.
    if status in {"staged", "applied"}:
        st.error("Invalid write status for a review-only proposal. No action allowed.")
        st.stop()
    left, right = st.columns(2)
    with left:
        hold = st.button(
            "Record Needs Review / Hold",
            disabled=status != "pending",
            use_container_width=True,
            key=f"hold_{proposal_id}",
        )
    with right:
        reject = st.button(
            "Reject review proposal",
            disabled=status != "pending",
            use_container_width=True,
            key=f"reject_{proposal_id}",
        )
    if hold or reject:
        decisions[proposal_id] = "needs_review" if hold else "rejected"
        save_decisions(decisions)
        st.rerun()
    if status == "needs_review":
        st.info("Needs Review / Hold recorded. Further independent evidence "
                "is required. No CRM account or contact was modified.")
    elif status == "rejected":
        st.info("Review proposal rejected. No CRM account or contact was modified.")
    else:
        st.info("Awaiting reviewer disposition. This review-only proposal "
                "cannot stage or apply a CRM change.")
else:
    if status == "needs_review":
        if action == "RENAME":
            st.info("Rename held for further evidence. No CRM write was made.")
        else:
            st.error("Unexpected hold status on a write-capable proposal.")
        st.stop()
    if action == "RENAME":
        left, middle, right = st.columns(3)
    else:
        left, right = st.columns(2)
    with left:
        stage = st.button(
            "Stage approval",
            disabled=(status != "pending" or action == "REVIEW_NAME_COLLISION"),
            use_container_width=True,
            key=f"stage_{proposal_id}",
        )
    hold = False
    if action == "RENAME":
        with middle:
            hold = st.button(
                "Needs Review / Hold",
                disabled=status != "pending",
                use_container_width=True,
                key=f"hold_{proposal_id}",
            )
    with right:
        reject = st.button(
            "Reject",
            disabled=status != "pending",
            use_container_width=True,
            key=f"reject_{proposal_id}",
        )

    if stage or reject or hold:
        decisions[proposal_id] = (
            "staged" if stage else "needs_review" if hold else "rejected"
        )
        save_decisions(decisions)
        st.rerun()

    if status == "staged":
        st.warning("Approval staged. No CRM change has yet been executed by this decision.")
        st.subheader("Apply approved correction")
        if action == "REPARENT":
            st.write("Correct the approved name and/or parent on the existing account.")
            callback = apply_reparent
        elif action == "CHOW":
            st.write("Create or recover the CHOW successor, then link the original account.")
            callback = apply_chow
        elif action == "FIX_ZIP":
            st.write("Correct only the reviewed billing ZIP on the existing Portsmouth account.")
            callback = apply_zip
        elif action == "RESOLVE_DUPLICATE":
            st.write(
                "Move the approved contact, verify both contacts on the canonical "
                "account, then inactivate and link the duplicate account."
            )
            callback = apply_duplicate
        elif action == "CREATE":
            st.write(
                "Create or recover one new Bellhaven CRM account. "
                "Every existing CRM record must remain unchanged."
            )
            callback = apply_create
        elif action == "RENAME":
            st.write(
                "Update only the existing account name to the exact reviewed "
                "website name. Parent, contacts and financial history stay unchanged."
            )
            callback = apply_approved_rename
        else:
            st.error("This proposal type cannot be applied.")
            st.stop()

        if st.button(
            "Apply approved correction to CRM",
            type="primary",
            key=f"apply_{proposal_id}",
        ):
            try:
                message = callback(proposal_id)
            except Exception as error:
                st.error(f"CRM update failed: {error}")
                st.warning(
                    "Do not click Apply again until the error and live CRM "
                    "state have been investigated."
                )
            else:
                decisions[proposal_id] = "applied"
                save_decisions(decisions)
                st.success(message)
                st.rerun()

    elif status == "applied":
        st.success("Approved correction recorded as applied. See the live CRM evidence above.")
    elif status == "rejected":
        st.info("Proposal rejected. The decision is saved; no CRM change was performed.")
    else:
        if action == "REVIEW_NAME_COLLISION":
            st.info(
                "This proposed match joins facilities in different locations and "
                "cannot be approved. Reject the false match if you agree with the evidence."
            )
        else:
            st.info("Awaiting reviewer decision. No CRM changes have been made.")
