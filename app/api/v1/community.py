"""Demonstration sites, pest surveillance clusters and community resource persons (Version 2.0)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require
from app.core.errors import AppError, Conflict
from app.db.session import get_db
from app.models import (
    ClusterMembership,
    CommunityResourcePerson,
    DemonstrationSite,
    Farmer,
    FieldRecord,
    PestSurveillanceCluster,
    Programme,
)
from app.schemas.common import Page, PageParams, paginate
from app.schemas.community import (
    ClusterIn,
    ClusterOut,
    ClusterUpdate,
    CrpIn,
    CrpOut,
    CrpUpdate,
    DemoSiteIn,
    DemoSiteOut,
    DemoSiteUpdate,
    MembershipIn,
    MembershipOut,
    MembershipUpdate,
)
from app.services import configuration, crud
from app.services.locate import resolve
from app.services.scope import apply_scope

router = APIRouter(tags=["community, demonstrations & surveillance"])


def _page(items: list, total: int, params: PageParams) -> dict:
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


# -- demonstration sites ---------------------------------------------------------------------------------------------

def _check_criteria(db: Session, kind: str, status: str | None, answers: dict | None) -> None:
    """Configured *required* criteria must be met before activation. Nothing is enforced until criteria are configured."""
    if status in ("established", "active"):
        missing = configuration.missing_required(db, kind, answers, "met")
        if missing:
            raise AppError(422, "criteria_not_met", f"Required selection criteria not met: {', '.join(missing)}")


def _site_stats(db: Session, sites: list[DemonstrationSite]) -> list[DemoSiteOut]:
    ids = [s.id for s in sites] or [uuid.UUID(int=0)]
    stats = {row[0]: row for row in db.execute(
        select(FieldRecord.demonstration_site_id, func.count(), func.max(FieldRecord.observed_at))
        .where(FieldRecord.demonstration_site_id.in_(ids), FieldRecord.validation_status != "rejected")
        .group_by(FieldRecord.demonstration_site_id))}
    return [DemoSiteOut.model_validate(s).model_copy(update={
        "visit_count": stats[s.id][1] if s.id in stats else 0,
        "last_visit_at": stats[s.id][2] if s.id in stats else None}) for s in sites]


def _scoped_site(db: Session, site_id: uuid.UUID, current: CurrentUser) -> DemonstrationSite:
    site = crud.get_or_404(db, DemonstrationSite, site_id, "Demonstration site")
    current.ensure_access(site.geo_unit_id, site.organisation_id)
    return site


@router.get("/demonstration-sites", response_model=Page[DemoSiteOut])
def list_sites(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, category: str | None = None,
               status: str | None = None, search: str | None = None,
               current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(DemonstrationSite).order_by(DemonstrationSite.site_code)
    for column, value in ((DemonstrationSite.programme_id, programme_id), (DemonstrationSite.category, category),
                          (DemonstrationSite.status, status)):
        if value:
            stmt = stmt.where(column == value)
    if search:
        stmt = stmt.where(or_(DemonstrationSite.site_code.ilike(f"%{search}%"), DemonstrationSite.name.ilike(f"%{search}%")))
    stmt = apply_scope(stmt, current, DemonstrationSite.geo_unit_id, DemonstrationSite.organisation_id)
    items, total = paginate(db, stmt, params)
    return _page(_site_stats(db, items), total, params)


@router.post("/demonstration-sites", response_model=DemoSiteOut, status_code=201)
def create_site(body: DemoSiteIn, current: CurrentUser = Depends(require("community.manage")),
                db: Session = Depends(get_db)) -> DemoSiteOut:
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    criteria = configuration.clean_assessment(db, "demonstration_site", body.criteria_assessment, "met")
    _check_criteria(db, "demonstration_site", body.status, criteria)
    loc = resolve(db, current, farmer_id=body.host_farmer_id, farm_plot_id=body.farm_plot_id,
                  organisation_id=body.organisation_id, geo_unit_id=body.geo_unit_id)
    site = crud.create(db, current, DemonstrationSite, {
        **body.model_dump(), "criteria_assessment": criteria or None, "host_farmer_id": loc.farmer_id,
        "farm_plot_id": loc.farm_plot_id,
        "geo_unit_id": loc.geo_unit_id, "organisation_id": loc.organisation_id}, "demonstration_site")
    db.commit()
    db.refresh(site)
    return _site_stats(db, [site])[0]


@router.get("/demonstration-sites/{site_id}", response_model=DemoSiteOut)
def get_site(site_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)):
    return _site_stats(db, [_scoped_site(db, site_id, current)])[0]


@router.patch("/demonstration-sites/{site_id}", response_model=DemoSiteOut)
def update_site(site_id: uuid.UUID, body: DemoSiteUpdate, current: CurrentUser = Depends(require("community.manage")),
                db: Session = Depends(get_db)) -> DemoSiteOut:
    site = _scoped_site(db, site_id, current)
    data = body.model_dump(exclude_unset=True)
    if {"host_farmer_id", "farm_plot_id"} & data.keys():
        loc = resolve(db, current, farmer_id=data.get("host_farmer_id", site.host_farmer_id),
                      farm_plot_id=data.get("farm_plot_id", site.farm_plot_id), geo_unit_id=site.geo_unit_id)
        data.update(host_farmer_id=loc.farmer_id, farm_plot_id=loc.farm_plot_id)
    if data.get("status") in ("established", "active") and not (data.get("established_on") or site.established_on):
        raise AppError(422, "established_on_required", "Record the date the site was established")
    if "criteria_assessment" in data:
        data["criteria_assessment"] = configuration.clean_assessment(db, "demonstration_site", data["criteria_assessment"], "met")
    _check_criteria(db, "demonstration_site", data.get("status", site.status) if "status" in data else None,
                    data.get("criteria_assessment", site.criteria_assessment))
    crud.update(db, current, site, data, "demonstration_site")
    db.commit()
    db.refresh(site)
    return _site_stats(db, [site])[0]


# -- pest surveillance clusters --------------------------------------------------------------------------------------

def _cluster_stats(db: Session, clusters: list[PestSurveillanceCluster]) -> list[ClusterOut]:
    ids = [c.id for c in clusters] or [uuid.UUID(int=0)]
    members = dict(db.execute(select(ClusterMembership.cluster_id, func.count(func.distinct(
        func.coalesce(Farmer.household_code, Farmer.farmer_code))))
        .join(Farmer, Farmer.id == ClusterMembership.farmer_id)
        .where(ClusterMembership.cluster_id.in_(ids), ClusterMembership.status == "active")
        .group_by(ClusterMembership.cluster_id)).all())
    inspections = {row[0]: row for row in db.execute(
        select(FieldRecord.surveillance_cluster_id, func.count(), func.max(FieldRecord.observed_at))
        .where(FieldRecord.surveillance_cluster_id.in_(ids), FieldRecord.validation_status == "valid")
        .group_by(FieldRecord.surveillance_cluster_id))}
    return [ClusterOut.model_validate(c).model_copy(update={
        "member_households": members.get(c.id, 0),
        "inspections": inspections[c.id][1] if c.id in inspections else 0,
        "last_inspection_at": inspections[c.id][2] if c.id in inspections else None}) for c in clusters]


def _scoped_cluster(db: Session, cluster_id: uuid.UUID, current: CurrentUser) -> PestSurveillanceCluster:
    cluster = crud.get_or_404(db, PestSurveillanceCluster, cluster_id, "Surveillance cluster")
    current.ensure_access(cluster.geo_unit_id, cluster.organisation_id)
    return cluster


@router.get("/surveillance-clusters", response_model=Page[ClusterOut])
def list_clusters(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, status: str | None = None,
                  search: str | None = None, current: CurrentUser = Depends(require("module.read")),
                  db: Session = Depends(get_db)) -> dict:
    stmt = select(PestSurveillanceCluster).order_by(PestSurveillanceCluster.cluster_code)
    if programme_id:
        stmt = stmt.where(PestSurveillanceCluster.programme_id == programme_id)
    if status:
        stmt = stmt.where(PestSurveillanceCluster.status == status)
    if search:
        stmt = stmt.where(or_(PestSurveillanceCluster.cluster_code.ilike(f"%{search}%"),
                              PestSurveillanceCluster.name.ilike(f"%{search}%")))
    stmt = apply_scope(stmt, current, PestSurveillanceCluster.geo_unit_id, PestSurveillanceCluster.organisation_id)
    items, total = paginate(db, stmt, params)
    return _page(_cluster_stats(db, items), total, params)


@router.post("/surveillance-clusters", response_model=ClusterOut, status_code=201)
def create_cluster(body: ClusterIn, current: CurrentUser = Depends(require("community.manage")),
                   db: Session = Depends(get_db)) -> ClusterOut:
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    loc = resolve(db, current, organisation_id=body.organisation_id, geo_unit_id=body.geo_unit_id)
    if body.status == "active" and not body.established_on:
        raise AppError(422, "established_on_required", "Record the date the cluster was established")
    criteria = configuration.clean_assessment(db, "pest_surveillance_cluster", body.criteria_assessment, "met")
    _check_criteria(db, "pest_surveillance_cluster", body.status, criteria)
    cluster = crud.create(db, current, PestSurveillanceCluster, {**body.model_dump(), "geo_unit_id": loc.geo_unit_id,
                                                                 "criteria_assessment": criteria or None},
                          "pest_surveillance_cluster")
    db.commit()
    db.refresh(cluster)
    return _cluster_stats(db, [cluster])[0]


@router.get("/surveillance-clusters/{cluster_id}", response_model=ClusterOut)
def get_cluster(cluster_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                db: Session = Depends(get_db)) -> ClusterOut:
    return _cluster_stats(db, [_scoped_cluster(db, cluster_id, current)])[0]


@router.patch("/surveillance-clusters/{cluster_id}", response_model=ClusterOut)
def update_cluster(cluster_id: uuid.UUID, body: ClusterUpdate, current: CurrentUser = Depends(require("community.manage")),
                   db: Session = Depends(get_db)) -> ClusterOut:
    cluster = _scoped_cluster(db, cluster_id, current)
    data = body.model_dump(exclude_unset=True)
    if data.get("status") == "active" and not (data.get("established_on") or cluster.established_on):
        raise AppError(422, "established_on_required", "Record the date the cluster was established")
    if "criteria_assessment" in data:
        data["criteria_assessment"] = configuration.clean_assessment(db, "pest_surveillance_cluster",
                                                                     data["criteria_assessment"], "met")
    _check_criteria(db, "pest_surveillance_cluster", data.get("status") if "status" in data else None,
                    data.get("criteria_assessment", cluster.criteria_assessment))
    crud.update(db, current, cluster, data, "pest_surveillance_cluster")
    db.commit()
    db.refresh(cluster)
    return _cluster_stats(db, [cluster])[0]


@router.get("/surveillance-clusters/{cluster_id}/members", response_model=list[MembershipOut])
def list_members(cluster_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")),
                 db: Session = Depends(get_db)) -> list:
    _scoped_cluster(db, cluster_id, current)
    return list(db.scalars(select(ClusterMembership).where(ClusterMembership.cluster_id == cluster_id)
                           .order_by(ClusterMembership.created_at)).unique())


@router.post("/surveillance-clusters/{cluster_id}/members", response_model=MembershipOut, status_code=201)
def add_member(cluster_id: uuid.UUID, body: MembershipIn, current: CurrentUser = Depends(require("community.manage")),
               db: Session = Depends(get_db)) -> ClusterMembership:
    cluster = _scoped_cluster(db, cluster_id, current)
    farmer = crud.get_or_404(db, Farmer, body.farmer_id, "Farmer")
    current.ensure_access(farmer.geo_unit_id, farmer.primary_fpo_id)
    if db.scalar(select(ClusterMembership.id).where(ClusterMembership.cluster_id == cluster.id,
                                                    ClusterMembership.farmer_id == farmer.id)):
        raise Conflict("Farmer is already a member of this cluster")
    obj = crud.create(db, current, ClusterMembership, {**body.model_dump(), "cluster_id": cluster.id,
                                                       "is_demo": cluster.is_demo}, "cluster_membership")
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/cluster-memberships/{membership_id}", response_model=MembershipOut)
def update_member(membership_id: uuid.UUID, body: MembershipUpdate, current: CurrentUser = Depends(require("community.manage")),
                  db: Session = Depends(get_db)) -> ClusterMembership:
    membership = crud.get_or_404(db, ClusterMembership, membership_id, "Membership")
    _scoped_cluster(db, membership.cluster_id, current)
    crud.update(db, current, membership, body, "cluster_membership")
    db.commit()
    db.refresh(membership)
    return membership


# -- community resource persons --------------------------------------------------------------------------------------

def crp_out(crp: CommunityResourcePerson, current: CurrentUser) -> CrpOut:
    out = CrpOut.model_validate(crp)
    return out if current.has("farmer.read_pii") else out.model_copy(update={"phone": None})


def _scoped_crp(db: Session, crp_id: uuid.UUID, current: CurrentUser) -> CommunityResourcePerson:
    crp = crud.get_or_404(db, CommunityResourcePerson, crp_id, "Community resource person")
    current.ensure_access(crp.geo_unit_id, crp.organisation_id)
    return crp


def _check_crp_status(data: dict, crp: CommunityResourcePerson | None = None) -> None:
    status = data.get("status", crp.status if crp else None)
    completed = data.get("training_completed_on", crp.training_completed_on if crp else None)
    if status in ("trained", "active") and not completed:
        raise AppError(422, "training_completion_required",
                       "Record the training completion date before marking a CRP trained/active")


@router.get("/community-resource-persons", response_model=Page[CrpOut])
def list_crps(params: PageParams = Depends(), programme_id: uuid.UUID | None = None, status: str | None = None,
              gender: str | None = None, search: str | None = None,
              current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)) -> dict:
    stmt = select(CommunityResourcePerson).order_by(CommunityResourcePerson.crp_code)
    for column, value in ((CommunityResourcePerson.programme_id, programme_id), (CommunityResourcePerson.status, status),
                          (CommunityResourcePerson.gender, gender)):
        if value:
            stmt = stmt.where(column == value)
    if search:
        stmt = stmt.where(or_(CommunityResourcePerson.crp_code.ilike(f"%{search}%"),
                              CommunityResourcePerson.full_name.ilike(f"%{search}%")))
    stmt = apply_scope(stmt, current, CommunityResourcePerson.geo_unit_id, CommunityResourcePerson.organisation_id)
    items, total = paginate(db, stmt, params)
    return _page([crp_out(c, current) for c in items], total, params)


@router.post("/community-resource-persons", response_model=CrpOut, status_code=201)
def create_crp(body: CrpIn, current: CurrentUser = Depends(require("community.manage")),
               db: Session = Depends(get_db)) -> CrpOut:
    crud.get_or_404(db, Programme, body.programme_id, "Programme")
    _check_crp_status(body.model_dump())
    loc = resolve(db, current, farmer_id=body.farmer_id, organisation_id=body.organisation_id, geo_unit_id=body.geo_unit_id)
    crp = crud.create(db, current, CommunityResourcePerson, {
        **body.model_dump(), "farmer_id": loc.farmer_id, "geo_unit_id": loc.geo_unit_id,
        "organisation_id": loc.organisation_id}, "community_resource_person")
    db.commit()
    db.refresh(crp)
    return crp_out(crp, current)


@router.get("/community-resource-persons/{crp_id}", response_model=CrpOut)
def get_crp(crp_id: uuid.UUID, current: CurrentUser = Depends(require("module.read")), db: Session = Depends(get_db)):
    return crp_out(_scoped_crp(db, crp_id, current), current)


@router.patch("/community-resource-persons/{crp_id}", response_model=CrpOut)
def update_crp(crp_id: uuid.UUID, body: CrpUpdate, current: CurrentUser = Depends(require("community.manage")),
               db: Session = Depends(get_db)) -> CrpOut:
    crp = _scoped_crp(db, crp_id, current)
    data = body.model_dump(exclude_unset=True)
    _check_crp_status(data, crp)
    crud.update(db, current, crp, data, "community_resource_person")
    db.commit()
    db.refresh(crp)
    return crp_out(crp, current)
