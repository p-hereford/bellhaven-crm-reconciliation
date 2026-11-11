import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import rename_writer as rw


# Synthetic fixture: no private CRM files required.
PROPOSAL = {
    "proposal_id": "rename:DEMO-ACCOUNT-001",
    "action": "RENAME",
    "crm_account_id": "DEMO-ACCOUNT-001",
    "crm_name": "Example Senior Residence",
    "proposed_name": "Bellhaven of Exampleton",
    "revenue": 0,
    "outstanding_ar": 0,
    "website_address": {
        "street": "100 Example Lane",
        "city": "Exampleton",
        "state": "OH",
        "zip": "43000",
    },
}


def sample_account():
    address = PROPOSAL["website_address"]
    return {
        "account_id": PROPOSAL["crm_account_id"],
        "name": PROPOSAL["crm_name"],
        "parent_id": rw.BELLHAVEN_PARENT_ID,
        "billing_street": address["street"],
        "billing_city": address["city"],
        "billing_state": address["state"],
        "billing_zip": address["zip"],
        "care_type": "Skilled Nursing",
        "status": "Active",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
        "duplicate_of_account": "",
        "chow_current_account": "",
        "note": "",
        "phone": "555-0100",
    }


class RenameWriterTests(unittest.TestCase):

    def setUp(self):
        self.account = sample_account()

        self.patches = [
            patch.object(
                rw, "_headers",
                return_value={"Authorization": "Bearer OFFLINE-TEST"}
            ),
            patch.object(
                rw, "_same_location", return_value=True
            ),
            patch.object(
                rw, "get_account",
                return_value=deepcopy(self.account)
            ),
            patch.object(
                rw, "_all_accounts",
                return_value=[deepcopy(self.account)]
            ),
            patch.object(rw, "urlopen"),
        ]

        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_approval_required(self):
        with self.assertRaises(PermissionError):
            rw.apply_rename(PROPOSAL)

        rw.urlopen.assert_not_called()

    def test_read_only_preflight(self):
        result = rw.preflight_rename(PROPOSAL)

        self.assertEqual(
            result["state"], "READY_FOR_REVIEW"
        )
        rw.urlopen.assert_not_called()

    def test_name_only_patch(self):
        before = deepcopy(self.account)
        after = deepcopy(before)
        after["name"] = PROPOSAL["proposed_name"]

        rw.get_account.side_effect = [before, after]

        result = rw.apply_rename(
            PROPOSAL, approved=True
        )

        self.assertEqual(result["status"], "applied")
        self.assertTrue(
            result["protected_fields_verified"]
        )

        rw.urlopen.assert_called_once()

        request = rw.urlopen.call_args.args[0]

        self.assertEqual(request.get_method(), "PATCH")

        payload = json.loads(request.data.decode("utf-8"))

        self.assertEqual(
            payload,
            {"name": PROPOSAL["proposed_name"]}
        )

        self.assertTrue(
            request.full_url.endswith(
                "/accounts/" + PROPOSAL["crm_account_id"]
            )
        )

    def test_repeat_run_does_not_write(self):
        already_correct = deepcopy(self.account)
        already_correct["name"] = PROPOSAL["proposed_name"]

        rw.get_account.return_value = already_correct

        result = rw.apply_rename(
            PROPOSAL, approved=True
        )

        self.assertEqual(
            result["status"], "already_applied"
        )
        rw.urlopen.assert_not_called()

    def test_unexpected_name_blocks_write(self):
        changed = deepcopy(self.account)
        changed["name"] = "Unexpected Third Name"
        rw.get_account.return_value = changed

        with self.assertRaises(ValueError):
            rw.apply_rename(PROPOSAL, approved=True)

        rw.urlopen.assert_not_called()

    def test_financial_drift_blocks_write(self):
        changed = deepcopy(self.account)
        changed["outstanding_ar"] = 500
        rw.get_account.return_value = changed

        with self.assertRaises(ValueError):
            rw.apply_rename(PROPOSAL, approved=True)

        rw.urlopen.assert_not_called()

    def test_name_collision_blocks_write(self):
        other = deepcopy(self.account)
        other["account_id"] = "ANOTHER_ACCOUNT"
        other["name"] = PROPOSAL["proposed_name"]

        rw._all_accounts.return_value = [
            self.account, other
        ]

        with self.assertRaises(ValueError):
            rw.apply_rename(PROPOSAL, approved=True)

        rw.urlopen.assert_not_called()

    def test_unexpected_field_change_is_detected(self):
        before = deepcopy(self.account)
        after = deepcopy(before)

        after["name"] = PROPOSAL["proposed_name"]
        after["phone"] = "CHANGED"

        rw.get_account.side_effect = [before, after]

        with self.assertRaisesRegex(
            RuntimeError, "protected-field changes"
        ):
            rw.apply_rename(
                PROPOSAL, approved=True
            )

        rw.urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)