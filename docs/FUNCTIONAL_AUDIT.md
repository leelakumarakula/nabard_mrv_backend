# Functional audit – Coffee Climate Resilience MRV Platform

Audit date: 2 Nov 2026 · Scope: Version 2.0 programme ("Climate-Resilient Coffee Landscapes and Inclusive Rural
Livelihoods Programme") · Repositories: `mrv-backend` (FastAPI, SQL Server) and `mrv-frontend` (Angular 21).
All test data is synthetic: DEMO seed records, and records prefixed `AUDIT-` (automated tests, rolled back) or
`UI-AUDIT-` (browser run, kept in the development database).

## 1. How the audit was done

A feature counted as complete only when it worked from the screen through the API to the database.

| Check | Method | Result |
|---|---|---|
| Frontend ↔ API contract | Every `api.*()` call in the Angular code (176 distinct operations) matched against the backend's OpenAPI routes | 0 calls without a backend route |
| Every screen, every role | Chrome (headless) signed in as each of the 10 demo roles, opened every navigation page and the first record of every list, and recorded failed API calls, console errors and exceptions | 217 pages + 155 record pages, 0 issues after fixes (6 issue types found and fixed) |
| UI-driven lifecycle | Real forms and dialogs: register farmer → farm → plot → edit farm → enrolment (FPO coordinator); monitoring entry → submit & validate → evidence upload (field officer); results read back from the API | 9/9 steps passed after fixes |
| API end-to-end lifecycle | `tests/test_functional_audit.py`: new household through all 12 lifecycle steps, with database read-backs | Passed |
| Role × endpoint matrix | Every permission-protected route (all methods) called as each of the 9 non-admin roles; must return 403 exactly when the role lacks the permission | 9/9 roles passed; every route either declares a permission or is on a reviewed allow-list |
| Record-level scope | Scoped roles (state coordinator, FPO coordinator, field officer, farmer, research partner) checked across list endpoints and record history | Passed after fixes |
| Data consistency | New read-only checker `python -m app.services.integrity` (12 checks) on the test and development databases | 0 errors, 0 warnings after fixes |
| Regression | Backend suite 117 tests (99 existing + 18 new); frontend 13 unit tests; production build; `alembic check`; migration upgrade → downgrade → upgrade | All passing |

## 2. Module audit (seven workstreams)

All modules share the farmer → farm → plot registry: workstream records, monitoring and practices resolve their farmer,
geography and organisation from the selected plot (verified: record geography/farmer = plot owner's). Records are archived,
withdrawn or superseded rather than deleted (audit requirement); deletes exist only for mappings and participants.

| # | Module | Screens | Main API / tables | Create / read / update | Validation | Role access |
|---|---|---|---|---|---|---|
| 1 | Climate intelligence & early warning | `/modules/climate`, `/advisories`, farmer → advisories, `/my-advisories` | `field_records`, `advisories`, `advisory_deliveries` | Yes; issue/withdraw advisories; deliveries & responses | Record-type rules, prediction labelling | Verified |
| 2 | Soil & water management and conservation | `/modules/water`, `/practices` | `field_records` (measurement, irrigation, water balance, conservation), `practice_records` | Yes (practice edit added) | Units, plot required, derived fields | Verified |
| 3 | Pest & disease risk | `/modules/pest`, `/surveillance-clusters` | `field_records` (inspection, risk), `pest_surveillance_clusters`, `cluster_memberships` | Yes | Incidence derivation, cluster criteria checklist | Verified |
| 4 | Soil & microclimate monitoring | `/modules/soil`, `/devices` | `devices`, `device_calibrations`, sensor `field_records` | Yes | Device capability/status checks | Verified |
| 5 | Climate & land suitability / planning | `/modules/suitability` | suitability, site finding, plantation condition, survival `field_records` | Yes | Record-type rules | Verified |
| 6 | Farmer & community implementation | `/farmers`, `/organisations`, `/modules/farmer_fpo`, `/demonstration-sites`, `/crps` | `farmers`, `farms`, `farm_plots`, `programme_participations`, categories, trainings + participants, demo sites, CRPs | Yes (farm edit, participant removal added) | Consent before enrolment, codes unique, demographic field rules | Verified |
| 7 | MRV & verification | `/indicators`, `/baselines`, `/monitoring`, `/evidence`, `/mrv`, `/verification`, `/reports` | indicator, baseline, monitoring, calculation, result, review, decision, sample, report tables | Yes (monitoring edit/correction added) | Configurable ranges, duplicates, approval gate | Verified |

## 3. End-to-end MRV lifecycle (synthetic household `AUDIT-F-0001`)

| Step | What was verified | Result |
|---|---|---|
| Programme setup | Reporting period; indicator definition created, completed, submitted, approved; target created as *proposed* | Pass |
| Farmer registration | FPO coordinator registers within their FPO; duplicate code refused (409); re-enrolment updates the single enrolment and keeps its date; category assigned explicitly (basis recorded) | Pass (bug fixed) |
| Farm / plot | Farm and plot linked to the farmer; farm edited; farmer detail shows 1 farm, 1 plot, 1.0 ha | Pass |
| Baseline | Recorded, validated, internally reviewed, independently verified | Pass |
| Field intervention | Training with attendance (attendance locked once validated); practice recommended → field-observed | Pass (bug fixed) |
| Monitoring | Two readings on the plot; farmer/geography derived from the plot; a 250 % reading flagged as an exception by the indicator's configured range | Pass |
| Evidence | Photo uploaded to a reading, checksummed, accepted by the verifier | Pass |
| Calculation | Approved definition → *not* provisional; value 24 % = mean of exactly the two valid readings (lineage checked); baseline 18 %, change +6 | Pass |
| Review | Finding recorded, review completed by the internal reviewer | Pass |
| Independent verification | Decision by the independent verifier; purposive sample of the practice confirmed on site; practice marked verified | Pass |
| Audit history | Result history: calculate → submit for review → … → verification decision; sample history visible to the verifier | Pass (bug fixed) |
| Reporting | District report shows the verified 24 % result (not provisional) and the target still *proposed*; JSON, CSV, Excel and PDF exports contain the same figures | Pass |
| Correction loop | Reviewer requests correction → only the submitting role can resubmit → edit resets validation → resubmission refused until revalidated | Pass (bugs fixed) |

## 4. Data consistency

| Check | Result |
|---|---|
| Farmer / household duplicates (same name + phone; same name in one household; household code across districts) | None. Codes are unique in the database; possible duplicates are flagged for review, never merged automatically |
| Farm and plot relationships (active child of archived parent; record plot ≠ record farmer; plot area > farm area) | Fixed: 31 DEMO farms had plot areas larger than the farm area (seed generated them independently). Seed corrected; the 31 development-database DEMO farms updated |
| Indicator results vs source records (stored lineage; recomputation from current sources) | All current results match their sources |
| Proposed targets shown as achievements | No – targets carry their status; achieved values come only from calculated results |
| Provisional results identified | Fixed: the report's indicator-results section (screen, CSV, Excel, PDF) did not show the PROVISIONAL flag |
| Verification decisions: audit event, independence from submitter and reviewer, no verified record without a decision | All pass |
| Dashboard totals vs records | Registered and enrolled farmers, household funnel and state breakdown reconcile with database counts |

Run the same checks any time: `python -m app.services.integrity --programme CCRL-INCLUSIVE` (exit code 1 on errors).

## 5. Bugs found and fixed

| # | Severity | Area | Problem | Fix |
|---|---|---|---|---|
| 1 | High | Security | A Programme Admin could reset a Super Admin's password, lock the account or remove the Super Admin role (privilege escalation) | Only a Super Admin can change a Super Admin account |
| 2 | High | Permissions | Resubmitting a review task after a correction request needed only `mrv.read` – a research partner could resubmit | Requires the same permission as the original submission |
| 3 | High | Workflow | Monitoring records could not be corrected in the UI (no edit; "Submit & validate" hidden after a correction request) | Edit/Correct action and revalidation added |
| 4 | High | Registry | FPO coordinators saw an empty Geography list, so could not register farmers | Geography pickers include the user's organisation areas (record scope unchanged) |
| 5 | Medium | Privacy | Record history (with before/after values) readable outside a scoped user's geography | Geography check on record history |
| 6 | Medium | Audit | Verification-sample history refused (403) for everyone except admins, including the verifier | Sample history enabled |
| 7 | Medium | Data | Re-posting an enrolment (e.g. a status change) erased the enrolment date | Only fields sent are updated |
| 8 | Medium | Data | Training participants could be added/removed after validation or verification, changing verified participation counts | Participant list follows the record's edit rule |
| 9 | Medium | Reporting | PROVISIONAL flag missing from report indicator results and exports | Flag added to content, screen, CSV, Excel and PDF |
| 10 | Low | Privacy | Sensitive demographic field definitions listed to all users | Filtered without `farmer.read_sensitive` |
| 11 | Low | UI | Super Admin saw "My advisories" (404 errors – not linked to a farmer) | Shown only to accounts linked to a farmer |
| 12 | Low | UI | Empty demographic data displayed as `{}` | Shown as "—" |
| 13 | Low | Demo data | 31 DEMO farms smaller than their plots | Seed and development data corrected |
| UI gaps | – | Frontend | API existed but no screen: farm edit, practice-record edit, participant removal, admin temporary password, integration source edit | Added |

Test-harness corrections: the role-matrix test now reads routes through FastAPI 0.162's included-router API (an earlier
draft silently checked zero routes); the test client discards a failed request's work as production does.

## 6. Missing API endpoints

No frontend call lacks a backend route. Backend operations without a screen, by design: health checks, inbound
integration endpoints (machine-to-machine, `X-Source-Key`), `GET /reports/programme` (live report used by tests and API
clients), single-item reads where the list suffices, and `POST /integrations/sources/{code}/plot-maps` (superseded by
identity maps). Remaining small UI gap: linking an existing evidence file to a second record (`POST /evidence/{id}/links`).
Not built (need requirements or licences): offline field sync, geospatial/map endpoints, outbound SMS/WhatsApp/email.

## 7. Database

Schema matches the models (`alembic check` clean); all three migrations upgrade and downgrade cleanly on SQL Server.
Uniqueness is enforced for farmer/farm/plot/device/geography/organisation codes, enrolment per programme, category
assignment per farmer and indicator code + version. No cascading deletes (archive model). Duplicate *people* with
different codes cannot be prevented by a constraint – the integrity checker lists candidates for review. Development
database: 31 DEMO farm areas corrected; two synthetic browser-test farmers kept (`UI-AUDIT-252617-F1` from an
interrupted run with a farm only, `UI-AUDIT-303666-F1` with farm, plot, monitoring record and evidence).

## 8. Security and permissions – open items

| Item | Status / recommendation |
|---|---|
| No login throttling or account lockout (failed logins are audited only) | Needed before production – lockout threshold is a policy decision |
| Security headers (HSTS, CSP, frame options) | Set at the HTTPS reverse proxy |
| Session token in browser session storage, 8 h lifetime, no revocation list | Deactivated users are refused on their next request; consider shorter lifetime + refresh for production |
| Evidence uploads not virus-scanned | Add scanning before accepting real uploads |
| An FPO coordinator may register a farmer in any geography if the primary FPO is their own | Business rule – needs programme-owner decision |
| Secrets | Only in environment variables / `.env` (never in the database or repositories); use a secret manager in deployment |

## 9. Integration readiness

No live integration exists; mock adapters are labelled MOCK and placeholders refuse to run. Everything below must be
supplied by the source owner before a live connection can be built and tested.

| Source | What is ready | Needed to go live |
|---|---|---|
| FarmFuture Water Balance Engine (`water_management`, push) | Inbound endpoint `POST /api/v1/integrations/water_management/measurements`, key auth, mappings, quarantine, idempotency | Data contract: output indicator codes, units, method/model version, record ID scheme, plot identifiers (for identity maps), frequency and volume; the platform-issued inbound key handed to the owner |
| FarmFuture Climate Intelligence Engine (`climate_intelligence`, pull) | Source registered; `CLIMATE_ENGINE_BASE_URL` placeholder | API specification (endpoints, auth, pagination, rate limits, error codes), response schema, hazard and risk-level taxonomy, geography codes, model version, update schedule; API key/OAuth credentials; then an adapter is written against the spec |
| FarmFuture Pest & Disease Prediction Engine (`pest_prediction`, pull) | Source registered; `PEST_ENGINE_BASE_URL` placeholder | Same as above plus supported pests/diseases, risk scale and validation evidence for the model |
| IoT devices (SoilSync, Moisture Probe, MicroClime, RainSync) | Mock gateway; device registry, capability map, calibration records | Device models and quantities, gateway protocol (push or pull), device identifiers, parameters, units, sampling interval, calibration data, gateway credentials |
| IMD, satellite, Coffee Board, state, KVK data | Not connected | Licences/data-sharing agreements, access method, formats, update frequency |
| SMS / WhatsApp / IVR delivery | Deliveries and responses can be recorded or imported | Provider, account and API credentials, consent wording |

## 10. Programme-owner decisions still open

The 14 decisions in [FEATURE_STATUS.md](FEATURE_STATUS.md) (sixth state, master lists, indicator definitions,
beneficiary categories, demographic data, demonstration-site criteria, cluster rules, verification method, NABARD
templates, endline method, FarmFuture APIs, IoT schedule, data licences, privacy and retention), plus two raised by this
audit: login lockout policy, and whether a farmer's geography must lie within their FPO's area.

## 11. Production readiness

| Area | Assessment |
|---|---|
| Core workflows (registry → calculation → review → verification → reporting) | Ready for user acceptance testing with synthetic data |
| Role-based access and scoping | Ready – matrix-tested; open items in §8 |
| Data quality controls | Ready – validation, approval gate, lineage, integrity checker |
| Reporting | Internal reporting ready; official NABARD CCF-ID template pending |
| Integrations | Not ready – specifications and credentials outstanding (§9) |
| Security hardening | Not ready – lockout, proxy headers, upload scanning, secret manager |
| Operations | Partly ready – backup/restore procedure documented but not yet rehearsed; synchronous jobs (add a queue before large imports); no email delivery |
| Real beneficiary data | Not before approved definitions, privacy/retention rules and the security items above |

**Overall:** functionally complete for UAT and demonstration with synthetic data; not yet production-ready for real
beneficiary data. The blocking items are programme-owner approvals, integration specifications and security hardening,
not missing application functionality.
