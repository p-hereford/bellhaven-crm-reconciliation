"""Synthetic, credential-free demonstration of the Bellhaven reviewer workflow.

This is NOT the live CRM reviewer and contains NO real account or contact data.
It makes no network requests, writes no files and changes only Streamlit session state.
Run: python -m streamlit run demo_reviewer.py
"""

from copy import deepcopy

import streamlit as st

DEMO_PARENT = "DEMO-BELLHAVEN-PARENT"

PROPOSALS = [
    {
        "id": "DEMO-RENAME-1",
        "facility": "Bellhaven of Alderbrook (synthetic)",
        "action": "RENAME",
        "account_id": "DEMO-A001",
        "proposed_name": "Bellhaven of Alderbrook",
        "website_evidence": {
            "name": "Bellhaven of Alderbrook",
            "street": "42 Maple Lane",
            "city": "Alderbrook",
            "state": "OH",
            "zip": "43000",
            "care_offerings": ["Assisted Living"],
            "source": "Synthetic example, not a real website",
        },
        "reason": "Matching physical address and parent, but a previous display name.",
    },
    {
        "id": "DEMO-CHOW-1",
        "facility": "Bellhaven of Exampleton (synthetic)",
        "action": "CHOW",
        "account_id": "DEMO-A002",
        "website_evidence": {
            "name": "Bellhaven of Exampleton",
            "street": "60 Example Drive",
            "city": "Exampleton",
            "state": "PA",
            "zip": "17000",
            "care_offerings": ["Skilled Nursing"],
            "source": "Synthetic example, not a real website",
        },
        "reason": (
            "Both historical revenue and outstanding receivables are positive. "
            "Preserve the former-owner account and link a new current-owner account."
        ),
    },
    {
        "id": "DEMO-HOLD-1",
        "facility": "Bellhaven of Quietwater (synthetic)",
        "action": "REVIEW_WEBSITE_ABSENCE",
        "account_id": "DEMO-A003",
        "website_evidence": {
            "finding": "No corresponding website entry in the synthetic snapshot",
            "limitation": "Absence does not establish closure",
        },
        "reason": "Do not deactivate an account based solely on website absence.",
    },
    {
        "id": "DEMO-REJECT-1",
        "facility": "Bellhaven of Oak Grove (synthetic)",
        "action": "REVIEW_NAME_COLLISION",
        "account_id": "DEMO-A004",
        "website_evidence": {
            "name": "Bellhaven of Oak Grove",
            "street": "15 Oak Road",
            "city": "Oak Grove",
            "state": "OH",
            "zip": "43001",
        },
        "reason": (
            "A similarly named CRM account is at 99 Pine Road in another city. "
            "A name-only match must not trigger a change."
        ),
    },
]

BASELINE_ACCOUNTS = {
    "DEMO-A001": {
        "account_id": "DEMO-A001",
        "name": "Alderbrook Senior Residence",
        "parent_id": DEMO_PARENT,
        "billing_street": "42 Maple Ln",
        "billing_city": "Alderbrook",
        "billing_state": "OH",
        "billing_zip": "43000",
        "status": "Active",
        "care_type": "Assisted Living",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
        "contacts_count": 1,
        "chow_current_account": "",
    },
    "DEMO-A002": {
        "account_id": "DEMO-A002",
        "name": "Bellhaven of Exampleton",
        "parent_id": "DEMO-FORMER-OWNER",
        "billing_street": "60 Example Dr",
        "billing_city": "Exampleton",
        "billing_state": "PA",
        "billing_zip": "17000",
        "status": "Active",
        "care_type": "Skilled Nursing",
        "lifetime_revenue": 76000,
        "outstanding_ar": 4100,
        "contacts_count": 1,
        "chow_current_account": "",
    },
    "DEMO-A003": {
        "account_id": "DEMO-A003",
        "name": "Bellhaven of Quietwater",
        "parent_id": DEMO_PARENT,
        "billing_street": "7 Lakeside Road",
        "billing_city": "Quietwater",
        "billing_state": "OH",
        "billing_zip": "43002",
        "status": "Active",
        "care_type": "Skilled Nursing",
        "lifetime_revenue": 120000,
        "outstanding_ar": 6200,
        "contacts_count": 1,
        "chow_current_account": "",
    },
    "DEMO-A004": {
        "account_id": "DEMO-A004",
        "name": "Bellhaven of Oak Grove",
        "parent_id": "DEMO-UNRELATED-OWNER",
        "billing_street": "99 Pine Road",
        "billing_city": "Other City",
        "billing_state": "OH",
        "billing_zip": "43099",
        "status": "Active",
        "care_type": "Assisted Living",
        "lifetime_revenue": 5000,
        "outstanding_ar": 0,
        "contacts_count": 2,
        "chow_current_account": "",
    },
}


def apply_synthetic_change(proposal, accounts):
    """Mutate only a synthetic in-memory dictionary; never call an API."""
    account_id = proposal["account_id"]
    account = accounts[account_id]

    if proposal["action"] == "RENAME":
        if account["parent_id"] != DEMO_PARENT:
            raise ValueError("Parent check failed")
        account["name"] = proposal["proposed_name"]
        return "Synthetic name updated. Parent and balances unchanged."

    if proposal["action"] == "CHOW":
        if not (account["lifetime_revenue"] > 0 and account["outstanding_ar"] > 0):
            raise ValueError("CHOW financial precondition failed")
        successor_id = "DEMO-SUCCESSOR-1"
        if account["chow_current_account"] and account["chow_current_account"] != successor_id:
            raise ValueError("Account already linked to a different successor")
        if successor_id not in accounts:
            successor = deepcopy(account)
            successor.update({
                "account_id": successor_id,
                "parent_id": DEMO_PARENT,
                "lifetime_revenue": 0,
                "outstanding_ar": 0,
                "contacts_count": 0,
                "chow_current_account": "",
            })
            accounts[successor_id] = successor
        account["chow_current_account"] = successor_id
        return "Synthetic successor created and linked. Historical balances preserved."

    raise ValueError("This example does not permit a write")


st.set_page_config(page_title="Bellhaven | Synthetic reviewer demo", layout="wide")
st.title("Bellhaven CRM Review | Synthetic demonstration")
st.warning(
    "DEMO ONLY. All accounts, financial amounts and locations shown here are invented. "
    "No API token, network request or live CRM update is used."
)

if "demo_accounts" not in st.session_state:
    st.session_state.demo_accounts = deepcopy(BASELINE_ACCOUNTS)
if "demo_decisions" not in st.session_state:
    st.session_state.demo_decisions = {}

accounts = st.session_state.demo_accounts
decisions = st.session_state.demo_decisions

counts = {
    status: sum(decisions.get(p["id"], "pending") == status for p in PROPOSALS)
    for status in ("pending", "staged", "applied", "rejected", "needs_review")
}
cols = st.columns(5)
for col, (label, status) in zip(cols, (
    ("Awaiting review", "pending"),
    ("Approved, not applied", "staged"),
    ("Applied", "applied"),
    ("Rejected", "rejected"),
    ("Needs Review / Hold", "needs_review"),
)):
    col.metric(label, counts[status])

by_id = {p["id"]: p for p in PROPOSALS}
selection = st.selectbox(
    "Select a synthetic proposal",
    [p["id"] for p in PROPOSALS],
    format_func=lambda pid: (
        f"{by_id[pid]['facility']} | {by_id[pid]['action']} "
        f"[{decisions.get(pid, 'pending').upper()}]"
    ),
)
proposal = by_id[selection]
status = decisions.get(selection, "pending")

st.subheader(proposal["facility"])
st.write("**Action:**", proposal["action"])
st.write("**Reason:**", proposal["reason"])
st.write("**Synthetic website evidence:**")
st.json(proposal["website_evidence"])
st.write("**Synthetic CRM account (in-memory only):**")
st.json(accounts[proposal["account_id"]])

if proposal["action"] == "CHOW" and accounts["DEMO-A002"]["chow_current_account"]:
    st.write("**Synthetic new-owner successor:**")
    st.json(accounts["DEMO-SUCCESSOR-1"])

st.divider()
st.write("**Reviewer decision:**", status.upper())

if status == "pending":
    if proposal["action"] in {"RENAME", "CHOW"}:
        left, right, third = st.columns(3)
        if left.button("Stage approval", use_container_width=True):
            decisions[selection] = "staged"
            st.rerun()
        if right.button("Reject", use_container_width=True):
            decisions[selection] = "rejected"
            st.rerun()
        if third.button("Needs Review / Hold", use_container_width=True):
            decisions[selection] = "needs_review"
            st.rerun()
    else:
        left, right = st.columns(2)
        if left.button("Needs Review / Hold", use_container_width=True):
            decisions[selection] = "needs_review"
            st.rerun()
        if right.button("Reject", use_container_width=True):
            decisions[selection] = "rejected"
            st.rerun()

elif status == "staged":
    st.info("Approval staged; nothing has changed yet.")
    if st.button("Apply to SYNTHETIC in-memory CRM", type="primary"):
        try:
            result = apply_synthetic_change(proposal, accounts)
        except ValueError as error:
            st.error(str(error))
        else:
            decisions[selection] = "applied"
            st.session_state.demo_last_message = result
            st.rerun()

elif status == "applied":
    st.success(st.session_state.get("demo_last_message", "Synthetic change applied."))
else:
    st.info("Recorded without modifying the synthetic CRM account.")

st.divider()
if st.button("Reset synthetic demo"):
    st.session_state.demo_accounts = deepcopy(BASELINE_ACCOUNTS)
    st.session_state.demo_decisions = {}
    st.session_state.pop("demo_last_message", None)
    st.rerun()

st.caption(
    "This optional public demonstration illustrates the approval state machine. "
    "It is not evidence of live API writes or a replacement for the real reviewer."
)
