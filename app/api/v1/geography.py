import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, descendant_geo_ids, require
from app.core.errors import AppError
from app.db.session import get_db
from app.models import Farmer, GeoUnit, Organisation
from app.schemas.programme import GeoTreeNode, GeoUnitIn, GeoUnitOut, GeoUnitUpdate
from app.services import crud

router = APIRouter(prefix="/geo-units", tags=["geography"])

PARENT_TYPE = {"state": None, "district": "state", "cluster": "district"}


def _visible_geo_ids(db: Session, current: CurrentUser) -> set[uuid.UUID]:
    """Geography a scoped user may pick or browse: their assigned areas plus the areas of their organisations
    (an FPO coordinator registers farmers in the FPO's cluster). Record-level data scope is unchanged."""
    org_geo = set(db.scalars(select(Organisation.geo_unit_id).where(Organisation.id.in_(current.org_ids or {uuid.UUID(int=0)}),
                                                                   Organisation.geo_unit_id.is_not(None))))
    return current.geo_ids | (descendant_geo_ids(db, org_geo) if org_geo else set())


@router.get("", response_model=list[GeoUnitOut])
def list_geo_units(unit_type: str | None = None, parent_id: uuid.UUID | None = None, search: str | None = None,
                   include_archived: bool = False, current: CurrentUser = Depends(require("geo.read")),
                   db: Session = Depends(get_db)) -> list:
    stmt = select(GeoUnit).order_by(GeoUnit.unit_type, GeoUnit.name)
    if unit_type:
        stmt = stmt.where(GeoUnit.unit_type == unit_type)
    if parent_id:
        stmt = stmt.where(GeoUnit.parent_id == parent_id)
    if search:
        stmt = stmt.where(GeoUnit.name.ilike(f"%{search}%"))
    if not include_archived:
        stmt = stmt.where(GeoUnit.is_archived == false())
    if not current.all_scope:
        stmt = stmt.where(GeoUnit.id.in_(_visible_geo_ids(db, current) or {uuid.UUID(int=0)}))
    return list(db.scalars(stmt).unique())


@router.get("/tree", response_model=list[GeoTreeNode])
def geo_tree(current: CurrentUser = Depends(require("geo.read")), db: Session = Depends(get_db)) -> list[GeoTreeNode]:
    units = list(db.scalars(select(GeoUnit).where(GeoUnit.is_archived == false()).order_by(GeoUnit.name)).unique())
    farmers = dict(db.execute(select(Farmer.geo_unit_id, func.count()).where(Farmer.is_archived == false())
                              .group_by(Farmer.geo_unit_id)).all())
    fpos = dict(db.execute(select(Organisation.geo_unit_id, func.count()).where(Organisation.org_type == "fpo")
                           .group_by(Organisation.geo_unit_id)).all())
    nodes = {u.id: GeoTreeNode.model_validate(u).model_copy(update={"farmer_count": farmers.get(u.id, 0),
                                                                    "fpo_count": fpos.get(u.id, 0), "children": []})
             for u in units}
    roots: list[GeoTreeNode] = []
    for u in units:
        node = nodes[u.id]
        if u.parent_id and u.parent_id in nodes:
            nodes[u.parent_id].children.append(node)
        else:
            roots.append(node)

    def roll_up(node: GeoTreeNode) -> tuple[int, int]:
        for child in node.children:
            f, o = roll_up(child)
            node.farmer_count += f
            node.fpo_count += o
        return node.farmer_count, node.fpo_count

    for root in roots:
        roll_up(root)
    if current.all_scope:
        return roots

    allowed = _visible_geo_ids(db, current)

    def visible(node: GeoTreeNode) -> list[GeoTreeNode]:
        if node.id in allowed:
            return [node]
        return [n for child in node.children for n in visible(child)]

    return [n for root in roots for n in visible(root)]


@router.post("", response_model=GeoUnitOut, status_code=201)
def create_geo_unit(body: GeoUnitIn, current: CurrentUser = Depends(require("geo.manage")),
                    db: Session = Depends(get_db)) -> GeoUnit:
    expected_parent = PARENT_TYPE[body.unit_type]
    if expected_parent is None and body.parent_id:
        raise AppError(422, "invalid_parent", "A state cannot have a parent")
    if expected_parent:
        parent = db.get(GeoUnit, body.parent_id) if body.parent_id else None
        if parent is None or parent.unit_type != expected_parent:
            raise AppError(422, "invalid_parent", f"A {body.unit_type} must belong to a {expected_parent}")
    obj = crud.create(db, current, GeoUnit, body.model_dump(), "geo_unit")
    db.commit()
    return obj


@router.patch("/{geo_unit_id}", response_model=GeoUnitOut)
def update_geo_unit(geo_unit_id: uuid.UUID, body: GeoUnitUpdate, current: CurrentUser = Depends(require("geo.manage")),
                    db: Session = Depends(get_db)) -> GeoUnit:
    obj = crud.update(db, current, crud.get_or_404(db, GeoUnit, geo_unit_id, "Geography unit"), body, "geo_unit")
    db.commit()
    return obj
