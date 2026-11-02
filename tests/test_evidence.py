from sqlalchemy import select

from app.models import GeoUnit, PracticeRecord
from app.seed.demo import PLACEHOLDER_PDF, PLACEHOLDER_PNG


def _upload(user, name, content, mime, **form):
    return user.post("/evidence", files={"file": (name, content, mime)}, data=form)


def test_upload_link_and_download(api, db) -> None:
    field = api.as_("field")
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    clusters = list(db.scalars(select(GeoUnit.id).where(GeoUnit.parent_id == ap0.id)))
    practice = db.scalars(select(PracticeRecord).where(PracticeRecord.geo_unit_id.in_(clusters))
                          .order_by(PracticeRecord.created_at)).first()
    r = _upload(field, "photo.png", PLACEHOLDER_PNG, "image/png", entity_type="practice_record",
                entity_id=str(practice.id), purpose="practice_photo", description="Mulch photo")
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["size_bytes"] == len(PLACEHOLDER_PNG) and len(asset["checksum_sha256"]) == 64
    assert asset["links"][0]["entity_type"] == "practice_record" and asset["review_status"] == "pending"
    listed = field.get("/evidence", params={"entity_type": "practice_record", "entity_id": str(practice.id)}).json()
    assert any(a["id"] == asset["id"] for a in listed["items"])
    download = field.get(f"/evidence/{asset['id']}/download")
    assert download.status_code == 200 and download.content == PLACEHOLDER_PNG
    assert download.headers["cache-control"] == "private, no-store"


def test_upload_restrictions(api) -> None:
    field = api.as_("field")
    assert _upload(field, "a.exe", b"MZ...", "application/x-msdownload").status_code == 415
    assert _upload(field, "a.pdf", PLACEHOLDER_PNG, "image/png").status_code == 415  # extension mismatch
    assert _upload(field, "a.png", b"not really a png", "image/png").status_code == 415  # content mismatch
    assert _upload(field, "a.pdf", b"", "application/pdf").status_code == 422
    assert _upload(field, "ok.pdf", PLACEHOLDER_PDF, "application/pdf").status_code == 201


def test_gps_requires_consent(api) -> None:
    field = api.as_("field")
    r = _upload(field, "p.png", PLACEHOLDER_PNG, "image/png", latitude="12.3", longitude="75.8")
    assert r.status_code == 422 and r.json()["error"]["code"] == "location_consent_required"
    r = _upload(field, "p.png", PLACEHOLDER_PNG, "image/png", latitude="12.3", longitude="75.8", location_consent="true")
    assert r.status_code == 201 and r.json()["latitude"] is not None


def test_evidence_access_and_review(api, db) -> None:
    field, verifier, fpo_admin = api.as_("field"), api.as_("verifier"), api.as_("fpo_admin")
    asset = _upload(field, "own.png", PLACEHOLDER_PNG, "image/png").json()
    # Unlinked evidence is visible to its uploader and programme-wide roles, not to other scoped users
    assert fpo_admin.get(f"/evidence/{asset['id']}").status_code == 403
    assert verifier.get(f"/evidence/{asset['id']}").status_code == 200
    assert field.post(f"/evidence/{asset['id']}/review", json={"review_status": "accepted"}).status_code == 403
    reviewed = verifier.post(f"/evidence/{asset['id']}/review", json={"review_status": "accepted"}).json()
    assert reviewed["review_status"] == "accepted"


def test_personal_evidence_hidden_without_pii_permission(api) -> None:
    asset = _upload(api.as_("admin"), "id.pdf", PLACEHOLDER_PDF, "application/pdf", access_classification="personal").json()
    admin = api.as_("admin")
    role = next(r for r in admin.get("/admin/roles").json() if r["code"] == "verifier")
    admin.put(f"/admin/roles/{role['id']}/permissions", json={"permissions": [p for p in role["permissions"]
                                                                              if p != "farmer.read_pii"]})
    assert api.as_("verifier").get(f"/evidence/{asset['id']}").status_code == 403
