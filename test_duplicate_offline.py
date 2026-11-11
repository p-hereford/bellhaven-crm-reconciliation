"""Offline regression tests for the Owosso duplicate writer.

All CRM access is mocked. Running this file sends NO network requests.
"""

import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import duplicate_writer as writer

CANON = "001EGU7BMJ942ZTRE6"
DUP = "001QU150PM4Z15UA71"
GLORIA = "003VEEW4P875R8G3U4"
TRICIA = "003BT8QV7PS7S0XM0P"
PID = f"duplicate:{DUP}"
PARENT = writer.crm.BELLHAVEN_PARENT_ID
URL = "https://analyst-assessment-production.up.railway.app/communities/bellhaven-of-owosso"
WEBSITE = {
    "name": "Bellhaven of Owosso", "street": "1120 W Main St", "city": "Owosso",
    "state": "MI", "zip": "48867", "care_offerings": ["Assisted Living"],
    "source_url": URL,
}
PROPOSAL = {
    "proposal_id": PID, "action": "RESOLVE_DUPLICATE",
    "facility_name": "Bellhaven of Owosso", "canonical_account_id": CANON,
    "duplicate_account_id": DUP,
    "contact_ids_to_preserve": [GLORIA, TRICIA],
    "contact_ids_to_reassign": [TRICIA],
    "proposed_duplicate_status": "Inactive",
    "proposed_duplicate_of_account": CANON,
    "source_url": URL,
    "explanation": "Same facility, two records, preserve contacts",
}


def account(id_, street, phone):
    return {
        "account_id": id_, "name": "Bellhaven of Owosso",
        "parent_id": PARENT, "parent_name": "Bellhaven Senior Living (Parent Account)",
        "billing_street": street, "billing_city": "Owosso",
        "billing_state": "MI", "billing_zip": "48867",
        "care_type": "Assisted Living", "status": "Active",
        "lifetime_revenue": 0, "outstanding_ar": 0,
        "phone": phone, "duplicate_of_account": "", "chow_current_account": "",
        "note": "", "updated_at": "initial",
    }


def contact(id_, account_id, name):
    return {
        "contact_id": id_, "account_id": account_id, "name": name,
        "title": "Administrator" if id_ == GLORIA else "Admissions Director",
        "email": "" if id_ == GLORIA else "tlindqvist@bellhavenliving.com",
        "phone": "(517) 226-4961" if id_ == GLORIA else "(330) 955-9503",
        "is_active": True, "created_by_candidate": False, "updated_at": "initial",
    }


def scenario(mode="normal"):
    accounts = {
        CANON: account(CANON, "1120 W Main St", "(734) 503-1363"),
        DUP: account(DUP, "1120 West Main Street", "(330) 383-5991"),
    }
    contacts = {
        GLORIA: contact(GLORIA, CANON, "Gloria Lambert"),
        TRICIA: contact(TRICIA, DUP, "Tricia Lindqvist"),
    }
    transfers = []
    account_patches = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "proposals_duplicate_draft.json").write_text(
            json.dumps([PROPOSAL]), encoding="utf-8"
        )
        (folder / "bellhaven_facilities.json").write_text(
            json.dumps([WEBSITE]), encoding="utf-8"
        )
        (folder / "review_decisions.json").write_text(
            json.dumps({PID: "pending" if mode == "unapproved" else "staged"}),
            encoding="utf-8"
        )

        def get_account(id_, headers):
            assert headers == {"Authorization": "Bearer FAKE"}
            return copy.deepcopy(accounts[id_])

        def get_contact(id_, headers):
            return copy.deepcopy(contacts[id_])

        def contact_page(aid, headers, page):
            assert page == 1
            matches = [copy.deepcopy(c) for c in contacts.values()
                       if c["account_id"] == aid]
            return {"total": len(matches), "data": matches}

        def transfer(contact_id, account_id, headers):
            assert contact_id == TRICIA and account_id == CANON
            transfers.append((contact_id, account_id))
            if mode != "ignored_patch":
                contacts[contact_id]["account_id"] = account_id
            if mode == "lost_response" and len(transfers) == 1:
                raise TimeoutError("Simulated loss after contact was updated")

        def account_patch(account_id, changes, headers):
            assert account_id == DUP
            assert changes["status"] == "Inactive"
            assert changes["duplicate_of_account"] == CANON
            assert "note" in changes
            # Original account cannot be made inactive until both contacts moved.
            assert all(c["account_id"] != DUP for c in contacts.values())
            account_patches.append(changes)
            accounts[account_id].update(changes)

        with (
            patch.object(writer, "PROJECT", folder),
            patch.object(writer.crm, "_headers", return_value={"Authorization": "Bearer FAKE"}),
            patch.object(writer.crm, "get_account", side_effect=get_account),
            patch.object(writer, "_get_contact", side_effect=get_contact),
            patch.object(writer, "_contact_page", side_effect=contact_page),
            patch.object(writer, "_patch_contact", side_effect=transfer),
            patch.object(writer.crm, "_patch_account", side_effect=account_patch),
        ):
            if mode == "unapproved":
                try:
                    writer.apply_duplicate(PID)
                except ValueError as error:
                    assert "STAGED" in str(error)
                else:
                    raise AssertionError("Unapproved duplicate unexpectedly changed")
                assert not transfers and not account_patches
                print("PASS: unapproved duplicate blocked")
                return

            if mode == "ignored_patch":
                try:
                    writer.apply_duplicate(PID)
                except RuntimeError as error:
                    assert "not verified" in str(error)
                else:
                    raise AssertionError("Ignored contact PATCH was not detected")
                assert len(transfers) == 1 and not account_patches
                print("PASS: ignored contact PATCH blocked before inactivation")
                return

            if mode == "lost_response":
                try:
                    writer.apply_duplicate(PID)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("Simulated lost response did not occur")
                assert not account_patches

            result = writer.apply_duplicate(PID)
            assert "Owosso duplicate verified" in result
            assert len(transfers) == 1
            assert len(account_patches) == 1
            assert accounts[CANON]["status"] == "Active"
            assert accounts[DUP]["status"] == "Inactive"
            assert accounts[DUP]["duplicate_of_account"] == CANON
            assert set(c["account_id"] for c in contacts.values()) == {CANON}
            assert contacts[TRICIA]["email"] == "tlindqvist@bellhavenliving.com"
            assert accounts[DUP]["phone"] == "(330) 383-5991"

            # A repeated call while approval remains staged performs no writes.
            result2 = writer.apply_duplicate(PID)
            assert "Owosso duplicate verified" in result2
            assert len(transfers) == 1 and len(account_patches) == 1
            assert writer._journal()[PID]["state"] == "verified"
            print(f"PASS: {mode} | contact PATCHes: 1 | account PATCHes: 1 | safe rerun")


if __name__ == "__main__":
    scenario("normal")
    scenario("lost_response")
    scenario("ignored_patch")
    scenario("unapproved")
    print("OFFLINE DUPLICATE TESTS PASSED. No live CRM requests were sent.")
