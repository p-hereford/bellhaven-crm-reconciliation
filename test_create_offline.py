"""Offline tests for the Bellhaven missing-facility CREATE writer.

Every API call is patched: running this file cannot modify the live CRM.
"""

import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import create_writer as writer

PARENT = writer.crm.BELLHAVEN_PARENT_ID
PROPOSAL_ID = "create:amberly-manor"
NEW_ID = "001CREATETEST000001"
COLLISION_ID = "0015D74ZLRY810RGY5"
SOURCE = "https://analyst-assessment-production.up.railway.app/communities/amberly-manor"

FACILITY = {
    "name": "Amberly Manor",
    "street": "4390 Darrow Rd",
    "city": "Hudson",
    "state": "OH",
    "zip": "44236",
    "care_offerings": ["Assisted Living"],
    "source_url": SOURCE,
}
PROPOSAL = {
    "proposal_id": PROPOSAL_ID,
    "action": "CREATE",
    "facility_name": "Amberly Manor",
    "crm_account_id": None,
    "proposed_name": "Amberly Manor",
    "proposed_parent_id": PARENT,
    "proposed_care_type": "Assisted Living",
    "website_address": {
        "street": "4390 Darrow Rd", "city": "Hudson",
        "state": "OH", "zip": "44236",
    },
    "care_offerings": ["Assisted Living"],
    "source_url": SOURCE,
    "same_name_elsewhere": [{
        "account_id": COLLISION_ID, "name": "Amberly Manor",
        "city": "Colorado Springs", "state": "CO",
        "street": "918 S Nevada Ave",
    }],
    "same_city_accounts": [],
    "explanation": "New, geographically distinct account",
}
COLLISION = {
    "account_id": COLLISION_ID,
    "name": "Amberly Manor",
    "parent_id": "001JUNIPER0000000",
    "parent_name": "Juniper Point Healthcare (Parent Account)",
    "billing_street": "918 S Nevada Ave",
    "billing_city": "Colorado Springs",
    "billing_state": "CO",
    "billing_zip": "80903",
    "care_type": "Assisted Living",
    "lifetime_revenue": 0,
    "outstanding_ar": 0,
    "status": "Active",
    "note": "",
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def run_test(timeout=False):
    state = {COLLISION_ID: copy.deepcopy(COLLISION)}
    posts = []
    original_path = writer.PROJECT
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "proposals_missing_draft.json").write_text(
            json.dumps([PROPOSAL]), encoding="utf-8"
        )
        (project / "bellhaven_facilities.json").write_text(
            json.dumps([FACILITY]), encoding="utf-8"
        )
        (project / "review_decisions.json").write_text(
            json.dumps({PROPOSAL_ID: "staged"}), encoding="utf-8"
        )

        def fake_get(account_id, headers):
            assert headers["Authorization"] == "Bearer FAKE"
            return copy.deepcopy(state[account_id])

        def fake_list(headers):
            return copy.deepcopy(list(state.values()))

        def fake_post(req, timeout=20):
            assert req.get_method() == "POST"
            assert req.full_url.endswith("/accounts")
            assert req.get_header("Authorization") == "Bearer FAKE"
            data = json.loads(req.data)
            assert data["parent_id"] == PARENT
            assert data["care_type"] == "Assisted Living"
            assert "lifetime_revenue" not in data
            assert "outstanding_ar" not in data
            assert len(posts) == 0, "Duplicate POST"
            posts.append(data)
            state[NEW_ID] = {
                "account_id": NEW_ID,
                **{k: v for k, v in data.items() if k != "parent_id"},
                "parent_id": data["parent_id"],
                "parent_name": "Bellhaven Senior Living (Parent Account)",
                "lifetime_revenue": 0,
                "outstanding_ar": 0,
                "duplicate_of_account": "",
                "chow_current_account": "",
            }
            if timeout_scenario:
                raise TimeoutError("Response lost after account was created")
            return FakeResponse({"account_id": NEW_ID})

        timeout_scenario = timeout
        writer.PROJECT = project
        try:
            with (
                patch.object(writer.crm, "_headers", return_value={"Authorization": "Bearer FAKE"}),
                patch.object(writer.crm, "get_account", side_effect=fake_get),
                patch.object(writer.crm, "_all_accounts", side_effect=fake_list),
                patch.object(writer, "urlopen", side_effect=fake_post),
            ):
                if timeout:
                    try:
                        writer.apply_create(PROPOSAL_ID)
                    except TimeoutError:
                        pass
                    else:
                        raise AssertionError("Expected simulated network timeout")
                    assert len(posts) == 1
                    assert (project / "create_journal.json").exists()

                result = writer.apply_create(PROPOSAL_ID)
                assert NEW_ID in result, result
                assert len(posts) == 1
                assert state[COLLISION_ID] == COLLISION, "Original collision record changed"
                repeat = writer.apply_create(PROPOSAL_ID)
                assert "already exists" in repeat
                assert len(posts) == 1

                (project / "review_decisions.json").write_text(
                    json.dumps({PROPOSAL_ID: "rejected"}), encoding="utf-8"
                )
                try:
                    writer.apply_create(PROPOSAL_ID)
                except ValueError as exc:
                    assert "approved" in str(exc)
                else:
                    raise AssertionError("Writer bypassed reviewer approval")
        finally:
            writer.PROJECT = original_path
    print("PASS:", "recover after lost response" if timeout else "normal CREATE",
          "| POSTs:", len(posts), "| original account untouched")


def run_conflict():
    state = {COLLISION_ID: copy.deepcopy(COLLISION)}
    state["001ANOTHER00000001"] = {
        "account_id": "001ANOTHER00000001",
        "name": "Unrelated existing account",
        "billing_street": FACILITY["street"],
        "billing_city": FACILITY["city"],
        "billing_state": FACILITY["state"],
        "billing_zip": FACILITY["zip"],
        "parent_id": PARENT,
        "note": "",
    }
    old_path = writer.PROJECT
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "proposals_missing_draft.json").write_text(json.dumps([PROPOSAL]))
        (project / "bellhaven_facilities.json").write_text(json.dumps([FACILITY]))
        (project / "review_decisions.json").write_text(json.dumps({PROPOSAL_ID: "staged"}))
        writer.PROJECT = project
        try:
            with (
                patch.object(writer.crm, "_headers", return_value={"Authorization": "Bearer FAKE"}),
                patch.object(writer.crm, "_all_accounts", return_value=list(state.values())),
                patch.object(writer, "urlopen", side_effect=AssertionError("Unexpected POST")),
            ):
                try:
                    writer.apply_create(PROPOSAL_ID)
                except RuntimeError as error:
                    assert "already exists" in str(error)
                else:
                    raise AssertionError("Existing address conflict was not blocked")
        finally:
            writer.PROJECT = old_path
    print("PASS: conflicting location blocked before POST")


if __name__ == "__main__":
    run_test()
    run_test(timeout=True)
    run_conflict()
    print("OFFLINE CREATE TESTS PASSED. No live CRM requests were sent.")
