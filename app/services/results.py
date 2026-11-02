import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeoUnit, IndicatorResult, Organisation
from app.schemas.mrv import IndicatorResultOut


def with_scope_labels(db: Session, results: list[IndicatorResult]) -> list[IndicatorResultOut]:
    geo_ids = {r.scope_geo_unit_id for r in results if r.scope_geo_unit_id}
    org_ids = {r.scope_organisation_id for r in results if r.scope_organisation_id}
    geos = {g.id: g.label for g in db.scalars(select(GeoUnit).where(GeoUnit.id.in_(geo_ids or {uuid.UUID(int=0)}))).unique()}
    orgs = dict(db.execute(select(Organisation.id, Organisation.name).where(Organisation.id.in_(org_ids or {uuid.UUID(int=0)}))).all())
    out = []
    for r in results:
        label = geos.get(r.scope_geo_unit_id) or orgs.get(r.scope_organisation_id) or "Whole programme"
        out.append(IndicatorResultOut.model_validate(r).model_copy(update={"scope_label": label}))
    return out
