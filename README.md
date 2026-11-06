# Coffee Climate Resilience MRV – Backend

FastAPI (Python 3.13) API for the Coffee Climate Resilience MRV Platform, persisting to SQL Server. Implements the
Version 1 technical specification and the Version 2.0 programme scope ("Climate-Resilient Coffee Landscapes and
Inclusive Rural Livelihoods Programme"). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/V2_GAP_ANALYSIS.md](docs/V2_GAP_ANALYSIS.md) and the feature/decision status in
[docs/FEATURE_STATUS.md](docs/FEATURE_STATUS.md); the latest functional audit is in
[docs/FUNCTIONAL_AUDIT.md](docs/FUNCTIONAL_AUDIT.md).

## Prerequisites

- Python 3.13
- Microsoft ODBC Driver 17 (or 18) for SQL Server
- A SQL Server database (and a separate disposable test database)

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env   # then edit DATABASE_URL, TEST_DATABASE_URL, SECRET_KEY
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python -m app.seed demo --password "<choose-a-demo-password>"   # local only: synthetic DEMO data
```

URL-encode special characters in the database password (`@` → `%40`). `.env` is git-ignored – never commit it.

For a real environment use `python -m app.seed reference` and create the first administrator with
`MRV_ADMIN_PASSWORD=... python -m app.seed admin --email you@org --name "Your Name"` instead of demo data, and
load the pending-decisions register with `python -m app.seed decisions --programme <CODE>`.

## Run

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8010
```

- OpenAPI docs: http://localhost:8010/docs
- Liveness `GET /api/v1/health`, readiness `GET /api/v1/health/db`

## Demo accounts (synthetic data, local only)

`admin@demo.local`, `programme.admin@demo.local`, `coordinator.ap@demo.local` (Andhra Pradesh),
`fpo.admin@demo.local` (one grower group), `field.officer@demo.local` (Alluri Sitharama Raju district),
`mrv.analyst@demo.local`, `internal.reviewer@demo.local`, `verifier@demo.local` (independent verifier),
`farmer@demo.local`, `researcher@demo.local` – all with the password given to `app.seed demo`.

## Database migrations (Alembic)

```powershell
.\.venv\Scripts\alembic upgrade head                                   # apply
.\.venv\Scripts\alembic revision --autogenerate -m "describe change"   # create
.\.venv\Scripts\alembic downgrade -1                                   # roll back one step
.\.venv\Scripts\alembic check                                          # models vs. migrations
```

## Quality checks

```powershell
.\.venv\Scripts\ruff check app tests
.\.venv\Scripts\python -m pytest            # uses TEST_DATABASE_URL (see below); rebuilds it on every run
.\.venv\Scripts\python -m app.services.integrity --programme CCRL-INCLUSIVE   # data-consistency checks (read-only)
```

The test database named in `TEST_DATABASE_URL` must exist (create it once, empty, e.g. `CREATE DATABASE nabard_mrv_test`).
Every run drops and rebuilds all its tables, seeds demo data, rolls all migrations back with that data in place, then
rebuilds and reseeds – so never point `TEST_DATABASE_URL` at a database you want to keep. Almost every test needs it.

`APP_ENV` defaults to `production`. Development conveniences (password-reset token in the API response, loading demo
data) work only when `APP_ENV` is explicitly `local`, `development` or `test`.

## Data connectors and the farm map

Integrations → *Ready-made connectors* registers a pull source in one step (pick the programme it files records under):

| Connector | Data | Creates |
|---|---|---|
| `open_meteo_weather` | REAL – Open-Meteo, free, no key | `weather_estimate` (past days) and `weather_forecast` per located plot |
| `nasa_power_weather` | REAL – NASA POWER, free, no key | `weather_estimate` per located plot (a few days' delay) |
| `sentinel2_ndvi` | REAL – Sentinel-2 via Microsoft Planetary Computer, free, no key | `vegetation_index` (NDVI) from the latest cloud-free scene |
| `sample_climate_engine`, `sample_pest_engine`, `sample_water_engine` | SAMPLE – synthetic stand-ins for the FarmFuture engines | risk estimates / water-balance outputs, flagged DEMO |
| `sample_iot_gateway` | SAMPLE – synthetic readings for every active registered device | `sensor_observation`, flagged DEMO |

Live FarmFuture connectors (read-only GET calls with `FARMFUTURE_API_TOKEN`; FarmFuture is never written to):

| Connector | Data | Creates |
|---|---|---|
| `farmfuture_water_engine` | Water Balance Engine irrigation plan; **Import farms** links FarmFuture farms to plots | `water_balance_output` |
| `farmfuture_soil_microclimate` | **Import devices**, then daily SoilSync / MicroClime statistics (setting `series`: app, raw, calibrated or lab-anchored) and soil lab reports | `sensor_observation`, `soil_test` |
| `farmfuture_pest_scouting` | App scouting (plants inspected / with signs) | `pest_inspection` |
| `farmfuture_satellite` | Per-block NDVI / NDMI from FarmFuture's Sentinel-2 pass | `vegetation_index` |
| `farmfuture_advisories` | Pest, disease and irrigation advisories (`history_days` for earlier runs) | advisories (app delivery = sent only) |
| `farmfuture_weather` | MicroClime station days (plus up to `rain_history_days` of earlier daily rain) and the 5-day FarmFuture forecast | `climate_observation`, `weather_forecast` |

Order: import farms on the water-engine source first; the other FarmFuture connectors reuse those farm links.

Platform calculation (reads the platform's own records, calls no external service):

| Connector | Data | Creates |
|---|---|---|
| `platform_rainfall_status` | Station rain since 1 January vs the plot's 30-year normal, per month (run after the FarmFuture weather source, which supplies the station rain and the normals) | `rainfall_status` |

Nothing is fetched until someone clicks **Run pull** on the source. Located plot = a plot with latitude/longitude whose
farmer is enrolled in the programme. These are not IMD data; IMD and the FarmFuture APIs need agreements and API
documentation first. The **Farm map** (`/map`, API `GET /api/v1/map/plots`) shows located plots with the latest values;
without `farmer.read_pii` points are rounded to about 1 km. Outbound HTTPS to `api.open-meteo.com`,
`power.larc.nasa.gov` and `planetarycomputer.microsoft.com` (and map tiles from `tile.openstreetmap.org` in the browser)
must be allowed where the platform is hosted.

## Layout

```
app/
  main.py                 app, middleware (CORS, request IDs), error handlers
  core/                   settings, security, permissions/roles, dependencies, errors
  models/                 SQLAlchemy models (registry, workstreams, community, MRV, verification, integration, audit)
  schemas/                Pydantic request/response models
  services/               record types, validation, units, calculations, MRV workflow, verification sampling,
                          reports, evidence storage, integration pipeline and adapters
  api/v1/                 routers
  seed/                   reference data and synthetic V2 demo data
alembic/                  migrations
docs/                     architecture, operations, gap analysis
tests/                    pytest suite (unit, API, workflow, integration, end-to-end, V2 acceptance)
```
