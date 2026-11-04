# Feature status and pending programme-owner decisions

Status as of 2 Nov 2026 (after the functional audit – see [FUNCTIONAL_AUDIT.md](FUNCTIONAL_AUDIT.md)). The live version of this view is in the application under **Programme status & decisions**
(`GET /api/v1/system/feature-status`, `GET /api/v1/programme-decisions`), which also counts approvals as they happen.

Legend: **Implemented** – working and tested · **Configurable** – working; behaviour set by programme configuration ·
**Awaiting approval** – working with a provisional treatment until the programme owner decides · **Not started**.

## Features

| Area | Feature | Status | Notes |
|---|---|---|---|
| Programme | Programme versions, versioned targets (proposed/approved/revised), reporting periods | Implemented | No hard-coded targets |
| Programme | State / district / cluster geography | Configurable | Six states per the proposal: AP, Odisha, Nagaland, Arunachal Pradesh, Mizoram, Manipur; state-wise household/area targets (§5.1) |
| Registry | Households, farmers, farms, plots, consent, enrolment | Implemented | Stable IDs, unique codes |
| Inclusion | Beneficiary categories (woman beneficiary, tribal community, …) | Awaiting approval | Categories *proposed*; assigned manually with a recorded basis; never inferred |
| Inclusion | Demographic data fields | Configurable | Defined in Programme configuration; sensitive fields need `farmer.read_sensitive` and consent |
| Workstreams | Seven modules, record types, validation, data-nature labels | Implemented | |
| Field | Demonstration sites and visits | Implemented / configurable | Selection criteria checklist (empty until approved) |
| Field | Pest surveillance clusters and household membership | Implemented / configurable | Cluster criteria checklist (empty until approved) |
| Field | Community resource persons | Implemented | Competency method pending |
| MRV | Indicator catalogue, versioning, configurable formulas, preview | Configurable | Formulas combine whitelisted aggregation methods; previews are never stored |
| MRV | Stored indicator calculations | Awaiting approval | Only for approved definitions; demo/test uses clearly labelled PROVISIONAL mode |
| MRV | Internal review, independent decision, separation of duties | Implemented | Policies configurable in System settings |
| MRV | Independent verification sampling (random/census/purposive, visit checklist) | Configurable | Visit checklist empty until the verifier's methodology is approved |
| Reporting | Dashboards (progress vs targets, household funnel, state/district breakdown) | Implemented | Scoped by role and geography |
| Reporting | Configurable report templates; CSV, JSON, Excel and PDF exports | Implemented | |
| Reporting | Official NABARD CCF-ID template | Awaiting approval | Template registered as *awaiting official template* |
| Reporting | Endline / impact assessment | Awaiting approval | Disabled until methodology approved |
| Integrations | Adapters, mappings, identity maps, quarantine, replay, mock adapters | Implemented | |
| Integrations | Real public data: Open-Meteo weather + forecast, NASA POWER weather, Sentinel-2 NDVI (Planetary Computer) | Implemented | Free services, no key; gridded estimates and forecasts are labelled as such – not station or IMD data |
| Integrations | SAMPLE connectors standing in for the FarmFuture Climate, Pest and Water engines and the IoT gateway | Implemented | Synthetic data, source must be is_mock, records flagged DEMO; replace when the APIs are supplied |
| Integrations | Live FarmFuture / IMD / IoT feeds | Awaiting approval | No live data until contracts and access are confirmed |
| Platform | Data-consistency checker (`python -m app.services.integrity`) | Implemented | 12 read-only checks; run before reporting |
| Platform | Sign-in lockout, upload virus scanning, outbound SMS/email | Not started | Lockout awaits a policy decision |
| Platform | Farm map (OpenStreetMap): located plots coloured by practice progress, risk, NDVI or forecast rain | Implemented | Points rounded to ~1 km without farmer.read_pii; no plot boundaries or licensed layers yet |
| Platform | Offline field mode, plot boundaries / licensed geospatial layers | Not started | Needs confirmed requirements and licensed datasets |

## Pending programme-owner decisions

| Code | Decision needed | Provisional treatment in the platform |
|---|---|---|
| SIXTH_STATE | **Decided:** Odisha (proposal of 29 Oct 2026, §1 and §5.1) | Odisha configured; recorded as decided in the register |
| MASTER_LISTS | District, cluster and FPO/grower-group lists | Geography/organisations configurable; demo placement synthetic |
| INDICATOR_DEFINITIONS | Approved definitions, formulas, evidence and verification rules | Draft definitions; preview only; PROVISIONAL results in demo |
| BENEFICIARY_CATEGORIES | Definitions of woman beneficiary and tribal community | Categories *proposed*; manual assignment only |
| DEMOGRAPHIC_DATA | Which attributes may be collected, consent and access | Configurable fields; sensitive fields restricted |
| DEMO_SITE_CRITERIA | Site selection criteria, visit frequency and forms | Configurable checklist (empty) |
| CLUSTER_RULES | Cluster definition and household participation | Configurable checklist; participation = active membership |
| VERIFICATION_METHOD | Verifier, sampling methodology, evidence checklist | Seeded random / census / purposive; configurable checklist |
| NABARD_TEMPLATES | Official CCF-ID quarterly/annual templates | Internal templates; Excel/PDF exports |
| ENDLINE_METHOD | Endline / impact methodology | Endline report disabled |
| FARMFUTURE_APIS | FarmFuture capabilities, owners, API contracts | Placeholder sources; mock adapters labelled MOCK |
| IOT_SCHEDULE | Device models, quantities, protocols | Provisional device capability map; mock gateway |
| DATA_LICENCES | IMD, satellite, Coffee Board, state, KVK access | IMD, Coffee Board, state, KVK not connected; free Open-Meteo, NASA POWER and Sentinel-2 connectors available |
| PRIVACY_RETENTION | Consent, data-sharing and retention rules | Consent per farmer; role-based masking; retention not configured |
| LOGIN_LOCKOUT | Sign-in lockout / throttling policy (raised by the functional audit) | Failed sign-ins audited; no lockout yet |
| FPO_REGISTRATION_GEOGRAPHY | Must an FPO-registered farmer lie within the FPO's area? (raised by the functional audit) | Allowed when the farmer's primary FPO is the coordinator's |

When a decision is made: record it in the register, then update the matching configuration (geography flag, indicator
definition/status, category definition/status, checklist, report template or setting). No code change is required.
