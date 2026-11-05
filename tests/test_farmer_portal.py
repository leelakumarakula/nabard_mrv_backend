"""Farmer self-service "My farm": only the signed-in farmer's own data, without internal working detail."""

from sqlalchemy import select

from app.models import Farm, FarmPlot, FieldRecord, User


def _my_plot_ids(db) -> tuple[set, object]:
    farmer_id = db.scalar(select(User.farmer_id).where(User.email == "farmer@demo.local"))
    plot_ids = set(db.scalars(select(FarmPlot.id).join(Farm, Farm.id == FarmPlot.farm_id).where(Farm.farmer_id == farmer_id)))
    return plot_ids, farmer_id


def test_my_farm_shows_only_own_data_without_internal_detail(api, db) -> None:
    plot_ids, farmer_id = _my_plot_ids(db)
    plot = db.get(FarmPlot, next(iter(plot_ids)))
    template = db.scalars(select(FieldRecord).where(FieldRecord.farm_plot_id == plot.id)).first() or \
        db.scalars(select(FieldRecord).where(FieldRecord.farm_plot_id.is_not(None))).first()
    base = {c: getattr(template, c) for c in ("programme_id", "module_code", "record_type", "geo_unit_id", "data_nature",
                                               "observed_at", "attributes", "is_demo")}
    db.add(FieldRecord(**base, farm_plot_id=plot.id, title="hidden draft", submission_status="draft"))
    db.add(FieldRecord(**base, farm_plot_id=plot.id, title="hidden rejected", submission_status="submitted",
                       validation_status="rejected", validation_messages=["internal message"]))
    db.commit()

    r = api.as_("farmer").get("/me/farm")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["plots"] and {p["id"] for p in out["plots"]} == {str(i) for i in plot_ids}
    codes = {p["plot_code"] for p in out["plots"]}
    assert all(rec["plot_code"] in codes or rec["plot_code"] is None for rec in out["records"])
    titles = {rec["title"] for rec in out["records"]}
    assert "hidden draft" not in titles and "hidden rejected" not in titles
    for rec in out["records"]:
        assert not {"notes", "validation_messages", "validation_status"} & rec.keys()
        assert all(not f["name"].endswith(("_ref", "_id")) for f in rec["fields"])
    assert all("observed_by_id" not in p for p in out["practices"])
    assert out["farmer"]["farmer_code"] and "phone" not in out["farmer"]


def test_my_farm_needs_a_linked_farmer_and_own_plots(api, db) -> None:
    assert api.as_("analyst").get("/me/farm").status_code in (403, 404)  # staff are not farmers
    plot_ids, farmer_id = _my_plot_ids(db)
    other = db.scalars(select(FarmPlot.id).join(Farm, Farm.id == FarmPlot.farm_id).where(Farm.farmer_id != farmer_id)).first()
    assert api.as_("farmer").get(f"/me/plots/{other}/boundary").status_code == 403
    own = api.as_("farmer").get(f"/me/plots/{next(iter(plot_ids))}/boundary")
    assert own.status_code == 200 and own.json() == {"available": False, "reason": "not_linked"}  # demo plot, no FarmFuture link
