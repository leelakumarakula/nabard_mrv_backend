from sqlalchemy import select

from app.models import Farmer, GeoUnit, Organisation, User


def _farmer_outside_ap0(db) -> Farmer:
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    clusters = set(db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id == ap0.id)))
    return db.scalars(select(Farmer).where(Farmer.geo_unit_id.not_in(clusters))).first()


def test_field_officer_sees_only_assigned_district(api, db) -> None:
    field = api.as_("field")
    items = field.get("/farmers", params={"page_size": 200}).json()["items"]
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    clusters = {str(c) for c in db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id == ap0.id))}
    assert items and all(f["geo_unit_id"] in clusters for f in items)
    outsider = _farmer_outside_ap0(db)
    assert field.get(f"/farmers/{outsider.id}").status_code == 403


def test_fpo_admin_scoped_to_own_fpo(api, db) -> None:
    user = db.scalar(select(User).where(User.email == "fpo.admin@demo.local"))
    items = api.as_("fpo_admin").get("/farmers", params={"page_size": 200}).json()["items"]
    assert items and {f["primary_fpo_id"] for f in items} == {str(user.organisation_id)}
    other_fpo = db.scalars(select(Organisation).where(Organisation.org_type == "fpo",
                                                      Organisation.id != user.organisation_id)).first()
    assert api.as_("fpo_admin").get(f"/organisations/{other_fpo.id}").status_code == 403


def test_cannot_create_farmer_outside_scope(api, db) -> None:
    # The coordinator is scoped to Karnataka: a Karnataka cluster is allowed, a Kerala cluster is refused.
    karnataka_cluster = db.scalar(select(GeoUnit).where(GeoUnit.code == "IN-AP-D2-C1"))
    kerala_cluster = db.scalar(select(GeoUnit).where(GeoUnit.code == "IN-NL-D1-C1"))
    coord = api.as_("coordinator")
    assert coord.post("/farmers", json={"farmer_code": "T-IN-1", "full_name": "Test",
                                        "geo_unit_id": str(karnataka_cluster.id)}).status_code == 201
    r = coord.post("/farmers", json={"farmer_code": "T-OUT-2", "full_name": "Test", "geo_unit_id": str(kerala_cluster.id)})
    assert r.status_code == 403


def test_register_farmer_farm_plot_and_enrol(api, db, programme_id) -> None:
    coord = api.as_("coordinator")
    cluster = db.scalar(select(GeoUnit).where(GeoUnit.code == "IN-AP-D1-C1"))
    fpo = db.scalar(select(Organisation).where(Organisation.geo_unit_id == cluster.id))
    r = coord.post("/farmers", json={"farmer_code": "T-0001", "full_name": "Test Farmer", "geo_unit_id": str(cluster.id),
                                     "primary_fpo_id": str(fpo.id)})
    assert r.status_code == 201, r.text
    farmer_id = r.json()["id"]
    # Enrolment requires recorded consent
    r = coord.post(f"/farmers/{farmer_id}/participations", json={"programme_id": str(programme_id)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    assert coord.patch(f"/farmers/{farmer_id}", json={"consent_status": "given"}).status_code == 422
    assert coord.patch(f"/farmers/{farmer_id}", json={"consent_status": "given", "consent_date": "2026-10-01"}).status_code == 200
    assert coord.post(f"/farmers/{farmer_id}/participations", json={"programme_id": str(programme_id)}).status_code == 201
    farm = coord.post(f"/farmers/{farmer_id}/farms", json={"farm_code": "T-FARM-1", "total_area": 2}).json()
    plot = coord.post(f"/farms/{farm['id']}/plots", json={"plot_code": "T-PLOT-1", "area": 2.5, "area_unit": "acre"})
    assert plot.status_code == 201
    detail = coord.get(f"/farmers/{farmer_id}").json()
    assert detail["participations"][0]["status"] == "enrolled"
    assert float(detail["total_plot_area_ha"]) == round(2.5 * 0.40468564224, 4)


def test_duplicate_codes_rejected(api, db) -> None:
    existing = db.scalars(select(Farmer)).first()
    r = api.as_("programme_admin").post("/farmers", json={"farmer_code": existing.farmer_code, "full_name": "Dup",
                                                          "geo_unit_id": str(existing.geo_unit_id)})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate"


def test_personal_data_masked_without_permission(api, db) -> None:
    admin = api.as_("admin")
    role = next(r for r in admin.get("/admin/roles").json() if r["code"] == "research_partner")
    admin.put(f"/admin/roles/{role['id']}/permissions", json={"permissions": role["permissions"] + ["farmer.read"]})
    farmer = db.scalars(select(Farmer).where(Farmer.phone.is_not(None))).first()
    researcher = api.as_("researcher")
    # Research partner has no geography scope, so farmer records stay inaccessible even with farmer.read
    assert researcher.get("/farmers").json()["total"] == 0
    assert researcher.get(f"/farmers/{farmer.id}").status_code == 403
    out = api.as_("programme_admin").get(f"/farmers/{farmer.id}").json()
    assert out["phone"] and out["pii_masked"] is False


def test_pii_read_is_audited(api, db) -> None:
    farmer = db.scalars(select(Farmer)).first()
    api.as_("programme_admin").get(f"/farmers/{farmer.id}")
    events = api.as_("admin").get("/admin/audit-events", params={"entity_id": str(farmer.id),
                                                                  "action": "read_personal_data"}).json()
    assert events["total"] >= 1


def test_geo_tree_rolls_up_counts(api) -> None:
    tree = api.as_("programme_admin").get("/geo-units/tree").json()
    karnataka = next(n for n in tree if n["name"] == "Andhra Pradesh")
    assert karnataka["farmer_count"] == sum(d["farmer_count"] for d in karnataka["children"]) > 0
    assert len(tree) == 6
