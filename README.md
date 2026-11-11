# Bellhaven CRM reconciliation

A Python assessment project to reconcile Bellhaven Senior Living's published facility directory with an authenticated CRM. It uses evidence-based matching, a human approval interface and a separately scheduled **read-only** monitor. No scheduled job is authorised to modify CRM accounts or contacts.

## Verified assessment result

| Measure | Verified result |
| --- | ---: |
| Published facilities discovered | 35 |
| Active Bellhaven facility accounts matched by address and name | 34 |
| Legitimate billing-address exception (Ashtabula PO Box) | 1 |
| Unresolved current-facility matches | 0 |
| Live CRM accounts after corrections | 127 |
| Reviewer decisions | 19 applied, 1 rejected, 7 held for investigation |
| Daily monitor | Two consecutive local read-only runs passed, with zero CRM writes |

The seven Holds are not resolved operational cases. They preserve potentially historical or website-absent records pending further evidence. The CRM was not automatically modified for those findings.

## How the system works

1. **Discovery:** `main.py` follows the paginated `/communities` directory and the site's homepage links. The homepage supplies an additional Findlay facility not covered by the paginated listing. `facility_probe.py` extracts address and care offerings from individual facility pages.
2. **Matching:** Physical street, city, state and ZIP are used to establish facility identity. Name-only matches are insufficient. Normalisation recognises common street abbreviations. Ashtabula's CRM billing PO Box is kept distinct from its published physical address.
3. **Reviewer workflow:** `review_app.py` shows source evidence and current live CRM state. The reviewer can stage approval, apply an approved change or reject it. Uncertain situations can be explicitly placed on Hold. Decisions are persisted locally and completed proposals are not resubmitted automatically.
4. **Guarded writes:** `crm_writer.py`, `create_writer.py`, `zip_writer.py`, `duplicate_writer.py` and `rename_writer.py` enforce action-specific preconditions. Name corrections patch only `name`; ZIP corrections patch only the reviewed billing ZIP. The duplicate-resolution flow preserves contacts. If a change of ownership involves **both** positive lifetime revenue and positive outstanding receivables, the historical account and balances are preserved and a separately parented successor is linked using `chow_current_account`.
5. **Daily monitoring:** `daily_monitor.py` runs the existing scraper in an isolated temporary directory, fetches fresh CRM data using GET requests and writes a local `latest_daily_report.json`. It never runs CRM write callbacks or changes the original website snapshot, original CRM baseline or reviewer decisions. New or removed website entries and unresolved matches require human investigation.

## Install and run the public read-only monitor

Python 3.12 is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env` file containing the assessment CRM token:

```dotenv
CRM_API_TOKEN=replace_with_your_own_token
```

The `.env` file must **never** be committed. Do not use the placeholder as a real credential. A valid, authorised assessment token and access to the supplied website/API are required for live CRM checks.

```bash
python daily_monitor.py
```

The command writes `latest_daily_report.json` locally, reporting facility coverage, exceptions and review findings. It makes **no** CRM modifications. The checked-in `bellhaven_facilities.json` is a public-website reference snapshot, not a copy of private CRM data.

## Human approval interface

On the original assessment workstation, with the private proposal files and `review_decisions.json` present, run:

```bash
python -m streamlit run review_app.py
```

The reviewer fetches live CRM evidence before approval. A proposal must be explicitly staged and then applied; an ordinary monitoring run never approves changes. The actual proposal and decision JSON files contain account-level evidence and are intentionally excluded from this public repository. **Consequently, a clean public clone can run the read-only monitor with authorised credentials, but cannot launch the populated reviewer without the private local proposal data.** A separate synthetic demonstration fixture, if distributed, must not be confused with the live decision history.

## Testing

Offline writer-safety tests are provided:

```bash
python test_writer_guard.py
python test_chow_offline.py
python test_create_offline.py
python test_duplicate_offline.py
python test_rename_offline.py
```

These use mocks and are intended not to write to the live CRM. `audit_full_coverage.py` checks current facility coverage against live CRM records on the original working environment. Successful writer unit tests are not a substitute for a full hosted-workflow test.

## Scheduled execution

`.github/workflows/daily-monitor.yml` runs the **read-only** monitor daily at **16:17 UTC**, and also supports manual `workflow_dispatch`. Its `CRM_API_TOKEN` must be configured as a GitHub Actions repository secret. It uploads an aggregate-only report and flags changed website entries or unresolved matches. GitHub schedules can be delayed. The hosted workflow should be manually run and verified after repository publication; local success does not establish hosted success.

## Safety and limitations

- No automated deactivation follows from website absence. The known website-absent accounts and competing historical ownership records remain review findings.
- Matching relies on the source website and account data being accurate; shared addresses and naming changes are not in themselves proof of historical ownership.
- The directory/reference snapshot has 35 facilities at the assessment date. A significant unexpected drop fails validation rather than silently deleting facility matches.
- A post-PATCH check detects unexpected protected-field changes, but does not automatically undo them; a partially completed write may require investigation and recovery.
- Private proposal JSON, decisions, account-level reports, lock files and API credentials are deliberately excluded from Git. The public `public_results_summary.json` contains only aggregate counts.

## Development and AI assistance

AI assistance was used in drafting and reviewing parts of the code, tests and documentation. The candidate operated the reviewer, approved or held the live changes and verified results against the API. The assessment write-up should separately report the candidate's **actual** time spent, rather than an estimate.
