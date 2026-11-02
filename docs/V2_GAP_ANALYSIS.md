# Version 2.0 gap analysis and traceability matrix

Source documents: *Coding Agent Implementation Specification v1.0* (1 Nov 2026) and *Updated Coding Agent
Specification v2.0* (2 Nov 2026, "Climate-Resilient Coffee Landscapes and Inclusive Rural Livelihoods Programme",
proposal dated 29 Oct 2026). Version 2.0 takes precedence for programme scope, targets, modules, beneficiary groups,
FarmFuture positioning, indicators and acceptance criteria; Version 1 remains the technical foundation.

## 1. State of the repository when Version 2.0 arrived

| Area | State |
|---|---|
| Backend (FastAPI, SQL Server, Alembic) | V1 scope complete: 46 tables, 161 API operations, 71 passing tests, lint clean, migration `b91ced985567` applied. |
| Seed data | V1 demo programme (8 states, 8,000 farmers, ₹30 cr) loaded in `nabard_mrv_db` – **must not be carried into the V2 programme**. |
| Frontend (Angular 21) | Core, shell, auth, dashboard, programmes, geography, organisations, farmers done. ~15 routed feature pages not yet written; build failing on missing modules. |
| Git | Neither folder is a git repository yet; nothing to preserve beyond the working tree. |

## 2. Gap summary

### Already completed (reused unchanged)
Authentication, password reset, role/permission catalogue, geography + organisation scoping, audit log, PII masking,
evidence storage (type/size/content checks, checksum, private download), integration framework (adapters, field
mappings, quarantine, replay, idempotency, sync-run history, dry-run, mock adapter), indicator versioning, baselines,
monitoring records with unit normalisation, separate lifecycle status dimensions, calculation runs with input lineage,
review tasks / findings / corrective actions / decisions with separation of duties, report snapshots with
status labels and CSV/JSON export, device registry and calibration, advisories/deliveries/responses, practice
records (recommended → reported → observed).

### Partially completed
| Requirement (V2 §) | Existing | Gap |
|---|---|---|
| Seven modules (§3) | Record-type framework for modules 1–7 | Module 2/5/6 scope widened; names changed. |
| Practice adoption stages (§3.2, §7) | recommended / reported / observed | No *verified* adoption stage. |
| Independent verification (§8, §9, §13) | Internal review + decision on a task | No separate Internal Reviewer vs Independent Verifier roles; no sample frame / selection method / visit result / discrepancy workflow. |
| Evidence linking (§8) | Links to any record | No evidence type/source; no link to demonstration site / surveillance cluster. |
| Adapters (§6.1) | Source IDs, original units, quarantine, replay | No received timestamp; identity maps limited to plots; no device-gateway mock; no device-parameter capability check. |
| Reports (§10, §11) | Programme/geography/FPO/module/indicator | No quarterly/annual/NABARD CCF-ID, demonstration, surveillance, inclusion, verification reports; stages planned→verified not separated. |
| Frontend (§10) | 8 of ~25 screens | Remaining screens + all V2 screens. |

### Needs modification
| Requirement | Change |
|---|---|
| Latest programme is primary (§1) | Programme gets version/proposal metadata and 36-month duration; V2 targets stored as versioned target records with status. V1 demo programme removed from the demo seed (never blended). |
| Six states (§1) | Demo geography = AP, Nagaland, Arunachal Pradesh, Mizoram, Manipur, Meghalaya (spec text). **Note:** the architecture diagram lists Odisha instead of Meghalaya – flagged for programme-owner confirmation; geography stays configurable. |
| Roles (§9) | Add *Internal Reviewer*; *Verifier* becomes *Independent Verifier*; FPO admin → *FPO/Grower Group Coordinator*; field officer → *Field Officer / Community Resource Person*. Verifier must differ from submitter and internal reviewer (configurable). |
| Calculations need approved definitions (§4, §13) | Calculation blocked unless indicator is approved; configurable *provisional* mode (demo/testing only) marks results PROVISIONAL. Indicator gets eligibility, exclusions and verification-sample-approach fields. |
| Households (§1, §4) | Targets count households: farmer gets a stable `household_code`; household counts use distinct households. |

### Not yet implemented
| Requirement | Planned implementation |
|---|---|
| Women & tribal participation (§3.6, §4) | Farmer `is_woman_beneficiary` (programme-defined), `social_category` / tribal flag as *sensitive* attributes behind a new `farmer.read_sensitive` permission; aggregated-only in reports. |
| Community resource persons (§3.6, §4) | `community_resource_persons` table: status nominated → in training → trained → active, training completion, competency evidence, gender. |
| Demonstration sites (§8) | `demonstration_sites` (category plantation / soil-moisture, site, plot, host, practices, status) + visits as `demonstration_visit` records with evidence. |
| Pest surveillance clusters (§3.3) | `pest_surveillance_clusters` + household membership; inspections linked to a cluster. |
| Soil & water conservation (§3.2) | `conservation_activity` record type (mulching, ground cover, contour bunds, terracing, trenches, moisture-retention pits, erosion control) + activity codes; adoption via practice records. |
| Plantation condition & young-plantation survival (§3.5) | `plantation_condition` and `plantation_survival_check` record types; shade & canopy management activity. |
| V2 indicators (§4) | Seven V2 indicators seeded as *Pending programme-owner approval* with new methods: households adopting, households with conservation, households in surveillance, women beneficiaries, women adopting, CRPs trained, women CRPs. |
| Independent verification sampling (§8) | `verification_samples` + `verification_sample_items`: frame description, selection method, seeded random draw, per-item visit result / discrepancy, verified adoption status, conclusion. |
| NABARD / quarterly / annual / thematic reports (§11) | New report types; *endline/impact* returns "methodology pending approval". |
| Dashboard progress vs targets (§11) | Target set with stage values (enrolled / reached / participating / adopted / verified). |

## 3. Delivery phases

| Phase | Scope | Gate |
|---|---|---|
| V2-1 | Schema migration + domain model (programme version, targets, inclusion fields, demo sites, clusters, CRPs, verification sampling, evidence/link fields, roles) | Migration applies on empty and existing DB; `alembic check` clean; existing tests pass. |
| V2-2 | Services & APIs (new entities, record types, calculation methods + approval gate, verification sampling workflow, integrations, reports, dashboard) | New API tests pass. |
| V2-3 | V2 demo seed (6 states, synthetic, labelled), re-seed dev DB, end-to-end tests | Full suite green. |
| V2-4 | Frontend: remaining V1 screens + V2 screens | `ng build` + unit tests pass. |
| V2-5 | Documentation, traceability update, known limitations, run/deploy guide | Handover checklist. |

## 4. Open questions for the programme owner (blocking production, not development)
1. Sixth state: Meghalaya (spec text) or Odisha (architecture diagram)?
2. Approved definitions for the seven V2 indicators (eligibility, denominators, evidence and verification rules).
3. Definition of "woman beneficiary" and which demographic attributes (e.g. tribal/ST status) may be recorded, by whom, with what consent.
4. Demonstration-site selection criteria, visit frequency and forms; surveillance-cluster definition and household membership rules.
5. Independent verifier appointment, sampling methodology and evidence checklist.
6. FarmFuture engines actually available, owners, API documentation and credentials; final IoT equipment schedule and protocols.
7. Official NABARD CCF-ID quarterly/annual report templates.

## 5. Delivery status (2 Nov 2026)

| Phase | Result | Verification |
|---|---|---|
| V2-1 Schema | Migration `40daf86f2089`: demonstration sites, surveillance clusters + memberships, CRPs, verification samples + items; programme version fields; versioned targets; household/inclusion fields; practice verification status; record links to sites/clusters; received timestamps; evidence type/source; indicator definition fields; provisional results; internal-reviewer tracking. | Applied to dev DB with data; full downgrade/upgrade round trip on every test run; `alembic check` clean. |
| V2-2 Services & APIs | Internal Reviewer vs Independent Verifier; approval-gated calculations; 7 new calculation methods (households adopting, women adopting, households in surveillance, women beneficiaries, CRPs trained, demo sites, active clusters); verification sampling; community APIs; sensitive-attribute controls; device capability checks; identity maps; payload defaults; mock IoT gateway; V2 report sections and 8 new report types; dashboard progress/funnel. | 186 API operations; lint clean. |
| V2-3 Seed & tests | V2 demo programme (6 states, 36 months, ₹15 cr not sanctioned, 15 proposed targets, 7 V2 indicators pending approval); dev DB re-seeded; earlier-proposal figures not loaded. | 91 backend tests pass (71 updated + 20 V2 acceptance tests). |
| V2-4 Frontend | All routed screens implemented, including demonstration sites, surveillance clusters, CRPs, verification samples, review tasks, integrations, reports, admin. | `ng build` clean; 10 unit tests pass; per-role API smoke test against the dev DB. |
| V2-5 Documentation | `docs/ARCHITECTURE.md` (architecture, ERD, lifecycle, security, integrations, deployment, backup/recovery, limitations); READMEs. | – |

Remaining dependencies are listed in §4 and in `docs/ARCHITECTURE.md` §8 (known limitations).

## 6. Programme-owner instructions of 2 Nov 2026 (configurable framework)

| Instruction | Delivered |
|---|---|
| 1. Sixth state | Geography `requires_confirmation` flag + note; Meghalaya flagged with the Odisha discrepancy; decision `SIXTH_STATE`. |
| 2. Indicators | `configurable_formula` method (safe arithmetic over whitelisted components), parameter validation on save, non-stored preview; stored calculations still require approval. |
| 3. Women & tribal participation | Configurable beneficiary categories + explicit assignments (basis recorded, consent required, never inferred); configurable demographic fields; sensitive masking. Migration `69f0d87a2d80` converted the former fixed columns. |
| 4. Field implementation | Configurable site/cluster criteria and verification visit checklist (enforced only for required items); purposive sample picker. |
| 5. Reporting | Report templates (sections), Excel and PDF exports, state/district dashboard breakdown; NABARD CCF-ID template *awaiting official template*. |
| Register | `programme_decisions` (14 items) + feature-status view; see [FEATURE_STATUS.md](FEATURE_STATUS.md). |

Verification: 99 backend tests (9 new), 12 frontend tests, migration round trip, live smoke test of exports/preview/breakdown.
