"""Verify that a CHOW proposal cannot use the REPARENT procedure.

Uses synthetic fixtures only. No live CRM requests.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import crm_writer as writer

proposal_id = "synthetic:chow-guard-001"

with tempfile.TemporaryDirectory() as directory:
    project = Path(directory)

    (project / "proposals.json").write_text(
        json.dumps([{
            "proposal_id": proposal_id,
            "action": "CHOW",
        }]),
        encoding="utf-8",
    )

    (project / "review_decisions.json").write_text(
        json.dumps({proposal_id: "staged"}),
        encoding="utf-8",
    )

    with (
        patch.object(writer, "PROJECT", project),
        patch.object(
            writer,
            "_headers",
            side_effect=AssertionError("Unexpected CRM access"),
        ) as crm_access,
    ):
        try:
            writer.apply_reparent(proposal_id)
        except ValueError as error:
            assert str(error) == (
                "This action requires another procedure"
            ), str(error)
        else:
            raise AssertionError("CHOW proposal was not blocked")

        crm_access.assert_not_called()

print("SAFETY CHECK PASSED")
print("CHOW proposal blocked from REPARENT procedure.")
print("Synthetic fixtures only. No live CRM requests.")
