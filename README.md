# Bellhaven CRM reconciliation

I compared Bellhaven Senior Living's published facility directory with the assessment CRM, investigated the discrepancies and applied the approved corrections. The website establishes which facilities Bellhaven currently advertises. It does not establish a facility's ownership history or whether a historical account should remain active. Unfortunately, the directory does not include an ownership ledger.

The reconciliation therefore distinguishes current facilities from accounts belonging to other parent organisations, duplicate records and coincidental name matches. CRM changes require a recorded reviewer decision. Daily monitoring is read-only.

## Results as at 11 November 2026

| Measure | Result |
| --- | ---: |
| Facilities identified on the website | 35 |
| Current CRM facilities matched by name and location | 34 |
| Documented billing-address exception, Ashtabula | 1 |
| Website facilities without a valid current CRM match | 0 |
| CRM accounts following approved corrections | 127 |
| Reviewer decisions | 19 applied, 1 rejected, 7 requiring further review |
| Daily monitoring | Two local runs and one GitHub-hosted run passed; 0 CRM writes |

All 35 published facilities have been accounted for. Ashtabula uses a PO Box for billing, whereas the website lists its physical address. I retained the distinction. There was insufficient evidence to conclude that either address was incorrect.

Seven proposals remain under review and no changes were made to the associated accounts. Four concern accounts under other parent organisations at addresses now associated with Bellhaven. Three concern Bellhaven accounts absent from the current website directory. Absence from the directory does not establish closure; nor does a shared address establish whether an earlier account ought to be deactivated. I rejected one proposed match because its name agreed but its location did not.

## Collection and matching

`main.py` follows the paginated `/communities` directory and checks the homepage for additional facility links. That additional check identified Findlay, which was not returned by the directory pages. `facility_probe.py` retrieves the individual facilities' addresses and advertised care offerings. The findings are saved in `bellhaven_facilities.json`, a snapshot of the public website at the time of collection.

Matching uses street address, city, state and ZIP code, with common street abbreviations normalised. A name match alone is insufficient: the dataset contains the same facility name at different locations. CRM billing addresses may also differ from a facility's physical address, as the Ashtabula record demonstrates. Neither distinction can safely be disregarded.

Each proposed correction appears in the reviewer alongside website evidence and a fresh read of the CRM account. Staging approval and applying a correction are separate operations. Rejected proposals and those requiring further investigation remain recorded.

The write procedures have distinct responsibilities:

- `crm_writer.py` handles reparenting and changes of ownership.
- `create_writer.py` checks for existing accounts before creation and can recognise a completed creation after an interrupted API response.
- `zip_writer.py` updates the approved billing ZIP field.
- `duplicate_writer.py` preserves contacts when resolving a duplicate account.
- `rename_writer.py` applies a name correction and verifies the resulting record.

Each procedure limits its submitted changes to the fields relevant to the approved action. This reduces the scope of possible side effects and permits the different failure conditions to be tested separately.

Changes of ownership require particular care when an original account has both positive lifetime revenue and positive outstanding receivables. In those circumstances, the original account retains its parent organisation and balances. A separate Bellhaven successor account is created and linked through `chow_current_account`. A tidy parent hierarchy is a poor reason to rewrite financial history.

## Installation and daily monitoring

Python 3.12 is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env` file containing an authorised assessment CRM token:

```dotenv
CRM_API_TOKEN=replace_with_your_own_token
```

Live CRM reads require this token. The `.env` file is excluded from Git and must not be committed.

```bash
python daily_monitor.py
```

`daily_monitor.py` obtains a fresh website snapshot in a temporary directory, retrieves current CRM accounts and compares the two. It writes `latest_daily_report.json` locally. It does not execute CRM write procedures or alter the existing reference snapshot and reviewer decisions.

The report distinguishes unmatched current facilities from other cases requiring investigation, including changed website listings, accounts absent from the current directory and accounts under different parent organisations at the same address.

## Reviewer applications

The populated live reviewer runs on the assessment workstation, where the private proposals and reviewer decisions are available:

```bash
python -m streamlit run review_app.py
```

These files contain account-level CRM information and have been excluded from the public repository. A fresh public checkout cannot reproduce the populated live reviewer without the private proposal data and authorised CRM access.

A separate demonstration reviewer is available:

```bash
python -m streamlit run demo_reviewer.py
```

`demo_reviewer.py` uses synthetic facilities and in-memory accounts. It demonstrates staging, applying, rejecting and marking a proposal for further review without credentials or CRM requests. Its simulated changes are not evidence of additional live CRM corrections.

## Tests and verification

The offline safety checks can be run individually:

```bash
python test_writer_guard.py
python test_chow_offline.py
python test_create_offline.py
python test_duplicate_offline.py
python test_rename_offline.py
```

All five test files passed from a fresh public GitHub checkout. They exercise action-type restrictions, approval requirements, duplicate prevention, recovery after an interrupted API response, contact preservation and repeat-run behaviour. They use mocks and synthetic or temporary fixtures. They verify the implemented logic under the conditions tested, rather than every failure a live API might produce.

`audit_full_coverage.py` compares the website facilities with current CRM records. The final live audit found 34 standard matches, one documented billing-address exception and no unmatched current facilities.

Two consecutive local monitoring runs returned identical counts. A manually triggered GitHub Actions execution also passed:

[GitHub Actions run 42976545749](https://github.com/p-hereford/bellhaven-crm-reconciliation/actions/runs/42976545749)

The hosted run, completed on 11 November 2026 UTC, reported 35 website facilities, 127 CRM accounts, no unresolved current-facility matches and no CRM writes.

## Schedule

`.github/workflows/daily-monitor.yml` is configured to run daily at 16:17 UTC and supports manual execution through `workflow_dispatch`. The CRM token is supplied through the repository's `CRM_API_TOKEN` secret. Published artefacts contain aggregate counts rather than account or contact details.

The workflow reports changes or discrepancies for human investigation. It cannot approve or apply corrections to the CRM. GitHub may delay scheduled executions. The manually triggered hosted run has passed; an unattended scheduled run has not yet been observed.

## Limits of the result

The website and CRM are both evidence sources, and either may contain omissions or inaccuracies. The historical ownership cases and three Bellhaven accounts absent from the current directory remain unresolved. I have not interpreted website absence as proof of closure.

The write procedures recheck relevant live fields before modification, and post-write checks detect unexpected differences. Such findings require investigation; the procedures do not automatically reverse an unexpected change. Where an API call succeeds but the response is lost, the relevant writer must establish whether the operation completed before proceeding.

The public repository contains source code, the public website snapshot, the synthetic demonstration, offline tests and aggregate results. Private proposals, account-level CRM exports, reviewer decisions, lock files and credentials are excluded.

## AI assistance and time spent

AI is not an operational component of this workflow. I used it to consult documentation more efficiently and reduce manual typing, as I would when developing a one-off script. The pipeline makes no LLM calls. Matching 35 facilities against the CRM using address, city, state and ZIP does not require one; deterministic matching also permits the relevant rules to be tested offline. An LLM could be added to assist with ambiguous name matches or reviewer notes, although those outputs would require separate evaluation. Such an addition would not require rebuilding the existing collection and matching procedures.

I did not require an LLM for the reconciliation itself. Its absence was a technical choice, rather than a limitation of what I can develop.
