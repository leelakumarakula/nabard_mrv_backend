from fastapi import APIRouter

from app.api.v1 import (
    admin,
    advisories,
    auth,
    community,
    configuration,
    dashboard,
    devices,
    evidence,
    farm_health,
    geography,
    health,
    indicators,
    integrations,
    me,
    monitoring,
    mrv,
    organisations,
    plot_map,
    programmes,
    registry,
    reports,
    verification,
    workstreams,
)

api_router = APIRouter()
for module in (health, auth, admin, dashboard, programmes, geography, organisations, registry, workstreams, devices,
               advisories, community, configuration, indicators, monitoring, evidence, mrv, verification, integrations,
               plot_map, reports, me, farm_health):
    api_router.include_router(module.router)
