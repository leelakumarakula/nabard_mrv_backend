# Feature status and pending programme-owner decisions

Status as of 6 Nov 2026 (after the functional audit – see [FUNCTIONAL_AUDIT.md](FUNCTIONAL_AUDIT.md)). The live version of this view is in the application under **Programme status & decisions**
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
| Integrations | FarmFuture Water Balance Engine (Workstream 2) | Implemented – test access | Live read-only pull (`farmfuture_water_engine`): daily water account and irrigation plan per identity-mapped farm, stored as *calculated* water-balance records. Tested with a 7-day test token; production needs a FarmFuture partner account |
| Integrations | FarmFuture soil & microclimate sensors (Workstream 4) | Implemented – test access | Live read-only pull (`farmfuture_soil_microclimate`): *Import devices* registers each SoilSync / MicroClime device on its linked plot; the pull stores the daily min / max / mean of soil moisture (two depths), soil temperature, pH, EC, N, P, K, air temperature, humidity, rain, pressure, wind, leaf wetness and light, plus soil lab reports that name a laboratory. Offline all-zero days are skipped. N/P/K in kg/ha and EC in µS/cm as labelled in the FarmFuture app; values match the FarmFuture app exactly (checked 6 Nov 2026). Setting `series` chooses the app, raw, calibrated or lab-anchored series and every reading names its series; readings outside the physically plausible range are kept as *exceptions* for an analyst. Leaf-wetness unit and the reporting series pending (FARMFUTURE_SENSOR_UNITS) |
| Integrations | FarmFuture on-farm weather & forecast (Workstream 1) | Implemented – test access | Live read-only pull (`farmfuture_weather`): daily rain, max / min temperature and mean humidity from the MicroClime stations as *climate observations* (linked to the station device), and FarmFuture's 5-day forecast per located farm as *weather forecasts* (whole India-time days only, kelvin converted to °C; the dataset names the forecast location FarmFuture used). `rain_history_days` (default 365) fills in the station's earlier daily rain (days not reported stay missing, never 0 mm; 2026 totals match FarmFuture's rain report). A station listed on two FarmFuture farms stays on the plot its device is registered on. FarmFuture publishes no climate-risk (drought / heat / excess-rain) API, so no risk levels are created |
| Integrations | FarmFuture pest & disease scouting (Workstream 3) | Implemented – test access | Live read-only pull (`farmfuture_pest_scouting`): app scouting records → scouting / inspection records (plants inspected / with signs, incidence calculated). Phone numbers, notes, photos and GPS points are not imported |
| Integrations | FarmFuture satellite block greenness (Workstream 5) | Implemented – test access | Live read-only pull (`farmfuture_satellite`): per-block NDVI mean / min / max, NDMI mean and declining cells from FarmFuture's latest Sentinel-2 pass, as vegetation-index records |
| Integrations | FarmFuture farm advisories (pest, disease, irrigation) | Implemented – test access | Live read-only pull (`farmfuture_advisories`): advisory-engine items (e.g. leaf rust, stalk rot, pests-and-diseases outlook, irrigate-by) → advisories for the farmer's area; app delivery recorded as *sent* only (FARMFUTURE_ADVISORY_DELIVERY). No risk level is given by FarmFuture, so none is stored |
| Integrations | Other live IMD / IoT gateway feeds; FarmFuture yield prediction, EUDR readiness, finances | Awaiting approval / not in scope | No live data until contracts and access are confirmed; FarmFuture write endpoints are never called |
| Platform | Data-consistency checker (`python -m app.services.integrity`) | Implemented | 12 read-only checks; run before reporting |
| Platform | Sign-in lockout, upload virus scanning, outbound SMS/email | Not started | Lockout awaits a policy decision |
| Platform | Farm map (MapLibre): located plots coloured by practice progress, risk, NDVI or forecast rain; 2D / 3D terrain, rotation, streets / satellite / relief, 3D columns; real farm outline for FarmFuture plots | Implemented | Points rounded to ~1 km without farmer.read_pii; outlines need farmer.read_pii and are read live from FarmFuture, never stored. Open layers only (OSM, AWS Terrain Tiles, Sentinel-2 cloudless 2016 by EOX, CC BY 4.0) – no licensed high-resolution imagery |
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
| FARMFUTURE_APIS | FarmFuture capabilities, owners, API contracts; whether a Climate Intelligence (drought / heat / excess-rain risk) API is planned | Live read-only connectors for water, soil & microclimate, scouting, satellite, advisories and weather (test token); no climate-risk API exists, so climate risk stays SAMPLE (labelled DEMO) |
| IOT_SCHEDULE | Device models, quantities, protocols | Provisional device capability map; mock gateway |
| DATA_LICENCES | IMD, satellite, Coffee Board, state, KVK access | IMD, Coffee Board, state, KVK not connected; free Open-Meteo, NASA POWER and Sentinel-2 connectors available |
| PRIVACY_RETENTION | Consent, data-sharing and retention rules | Consent per farmer; role-based masking; retention not configured |
| LOGIN_LOCKOUT | Sign-in lockout / throttling policy (raised by the functional audit) | Failed sign-ins audited; no lockout yet |
| FPO_REGISTRATION_GEOGRAPHY | Must an FPO-registered farmer lie within the FPO's area? (raised by the functional audit) | Allowed when the farmer's primary FPO is the coordinator's |
| FARMFUTURE_SENSOR_UNITS | Which FarmFuture sensor series counts for reporting (app / raw / calibrated / lab-anchored); leaf-wetness unit; MicroClime wind m/s or km/h | N/P/K kg/ha and EC µS/cm (as labelled in the app); app series imported (`series` = app); leaf wetness raw; wind m/s per the API |
| FARMFUTURE_ADVISORY_DELIVERY | Does an advisory shown in the FarmFuture app count as delivered/read; does app scouting count as surveillance? | App delivery recorded as *sent* only (not counted as reach); app scouting imported and labelled |

When a decision is made: record it in the register, then update the matching configuration (geography flag, indicator
definition/status, category definition/status, checklist, report template or setting). No code change is required.
