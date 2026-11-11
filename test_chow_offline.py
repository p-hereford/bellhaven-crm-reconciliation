"""Local CHOW rehearsal. No requests to the live CRM are possible.

Usage: .venv/bin/python test_chow_offline.py
"""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import crm_writer as writer

OLD_ID = "001U6RW32TY0WSXZZB"
NEW_ID = "001TESTCHOW000000001"
PROPOSAL_ID = f"ownership:{OLD_ID}"

PROPOSAL = {
    "proposal_id": PROPOSAL_ID,
    "action": "CHOW",
    "facility_name": "Bellhaven of Tiffin",
    "crm_account_id": OLD_ID,
    "crm_name": "Bellhaven of Tiffin",
    "proposed_name": "Bellhaven of Tiffin",
    "current_parent_id": "001FWSQ30SFW6S7604",
    "proposed_parent_id": writer.BELLHAVEN_PARENT_ID,
    "revenue": 84000,
    "outstanding_ar": 12400,
    "website_address": {
        "street": "45 St Lawrence Dr", "city": "Tiffin",
        "state": "OH", "zip": "44883",
    },
    "source_url": "https://analyst-assessment-production.up.railway.app/communities/bellhaven-of-tiffin",
}

ORIGINAL = {
    "account_id": OLD_ID,
    "name": "Bellhaven of Tiffin",
    "parent_id": "001FWSQ30SFW6S7604",
    "parent_name": "Cedar Trail Communities (Parent Account)",
    "billing_street": "45 St Lawrence Dr",
    "billing_city": "Tiffin",
    "billing_state": "OH",
    "billing_zip": "44883",
    "care_type": "Skilled Nursing",
    "lifetime_revenue": 84000,
    "outstanding_ar": 12400,
    "status": "Active",
    "duplicate_of_account": "",
    "chow_current_account": "",
    "note": "Historical account",
}


class FakeResponse:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self):
        return json.dumps(self.body).encode("utf-8")


def run_rehearsal(simulate_timeout=False):
    """Run the real writer against an entirely in-memory fake CRM."""
    state = {OLD_ID: copy.deepcopy(ORIGINAL)}
    calls = {"posts": 0, "patches": 0}
    old_project = writer.PROJECT

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "proposals.json").write_text(json.dumps([PROPOSAL]), encoding="utf-8")
        (directory / "review_decisions.json").write_text(
            json.dumps({PROPOSAL_ID: "staged"}), encoding="utf-8"
        )

        def fake_get_account(account_id, headers):
            assert headers["Authorization"] == "Bearer FAKE_OFFLINE_TOKEN"
            return copy.deepcopy(state[account_id])

        def fake_all_accounts(headers):
            return [copy.deepcopy(a) for a in state.values()]

        def fake_patch_account(account_id, changes, headers):
            assert account_id == OLD_ID, "Only the historical account may be linked"
            assert set(changes) == {"chow_current_account"}, "Other original fields changed"
            assert changes["chow_current_account"] == NEW_ID
            calls["patches"] += 1
            state[account_id].update(changes)

        def fake_urlopen(request, timeout=20):
            # This deliberately intercepts every possible network call.
            assert request.get_method() == "POST", "Unexpected network method"
            assert request.full_url.endswith("/accounts"), "Unexpected endpoint"
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["parent_id"] == writer.BELLHAVEN_PARENT_ID
            assert payload["name"] == PROPOSAL["proposed_name"]
            assert "lifetime_revenue" not in payload
            assert "outstanding_ar" not in payload
            assert calls["posts"] == 0, "Duplicate account creation attempted"
            calls["posts"] += 1
            state[NEW_ID] = {
                "account_id": NEW_ID,
                "name": payload["name"],
                "parent_id": payload["parent_id"],
                "parent_name": "Bellhaven Senior Living",
                "billing_street": payload["billing_street"],
                "billing_city": payload["billing_city"],
                "billing_state": payload["billing_state"],
                "billing_zip": payload["billing_zip"],
                "care_type": payload["care_type"],
                "status": payload["status"],
                "note": payload["note"],
                "lifetime_revenue": 0,
                "outstanding_ar": 0,
                "chow_current_account": "",
                "duplicate_of_account": "",
            }
            if simulate_timeout:
                raise TimeoutError("Simulated lost response AFTER server created the account")
            return FakeResponse({"account_id": NEW_ID})

        try:
            writer.PROJECT = directory
            with (
                patch.object(writer, "_headers", return_value={
                    "Authorization": "Bearer FAKE_OFFLINE_TOKEN"
                }),
                patch.object(writer, "get_account", side_effect=fake_get_account),
                patch.object(writer, "_all_accounts", side_effect=fake_all_accounts),
                patch.object(writer, "_patch_account", side_effect=fake_patch_account),
                patch.object(writer, "urlopen", side_effect=fake_urlopen),
            ):
                if simulate_timeout:
                    try:
                        writer.apply_chow(PROPOSAL_ID)
                    except TimeoutError:
                        pass
                    else:
                        raise AssertionError("Expected an interrupted response")
                    assert calls["posts"] == 1
                    assert state[OLD_ID]["chow_current_account"] == ""
                    assert state[NEW_ID]["parent_id"] == writer.BELLHAVEN_PARENT_ID
                    assert json.loads((directory / "chow_journal.json").read_text())[
                        PROPOSAL_ID
                    ]["state"] == "create_attempted"

                result = writer.apply_chow(PROPOSAL_ID)
                assert "verified" in result
                assert calls["posts"] == 1
                assert calls["patches"] == 1
                assert state[OLD_ID]["chow_current_account"] == NEW_ID
                assert {
                    key: value for key, value in state[OLD_ID].items()
                    if key != "chow_current_account"
                } == {
                    key: value for key, value in ORIGINAL.items()
                    if key != "chow_current_account"
                }
                assert state[NEW_ID]["outstanding_ar"] == 0
                assert state[NEW_ID]["lifetime_revenue"] == 0

                # An accidental second invocation must not create a second successor.
                repeat = writer.apply_chow(PROPOSAL_ID)
                assert "already linked and verified" in repeat
                assert calls["posts"] == 1
                assert calls["patches"] == 1

                # Check permission gate on a fresh call, too.
                (directory / "review_decisions.json").write_text(
                    json.dumps({PROPOSAL_ID: "pending"}), encoding="utf-8"
                )
                try:
                    writer.apply_chow(PROPOSAL_ID)
                except ValueError as exc:
                    assert "approved" in str(exc)
                else:
                    raise AssertionError("Writer bypassed reviewer approval")
        finally:
            writer.PROJECT = old_project

    print(
        "PASS:",
        "recovery after lost POST response" if simulate_timeout else "normal CHOW",
        "| POSTs:", calls["posts"], "| original-account PATCHes:", calls["patches"],
    )


if __name__ == "__main__":
    run_rehearsal()
    run_rehearsal(simulate_timeout=True)
    print("OFFLINE CHOW TESTS PASSED. No live CRM requests were sent.")
