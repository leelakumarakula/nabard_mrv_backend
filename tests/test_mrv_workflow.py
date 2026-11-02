from sqlalchemy import select

from app.models import IndicatorDefinition, ReportingPeriod, User


def _ids(db):
    period = db.scalar(select(ReportingPeriod).where(ReportingPeriod.code == "2026-Q4"))
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "SOIL_MOISTURE_MEAN"))
    reviewer = db.scalar(select(User).where(User.email == "internal.reviewer@demo.local"))
    return period, ind, reviewer


def _calculate(analyst, programme_id, period, ind, **extra):
    r = analyst.post("/mrv/calculations", json={"indicator_id": str(ind.id), "programme_id": str(programme_id),
                                                "reporting_period_id": str(period.id), **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_calculation_is_reproducible_and_traceable(api, db, programme_id) -> None:
    period, ind, _ = _ids(db)
    analyst = api.as_("analyst")
    out = _calculate(analyst, programme_id, period, ind)
    run, result = out["run"], out["result"]
    assert run["status"] == "calculated" and run["method_code"] == "monitoring_mean" and run["method_version"] == "1.0"
    assert run["input_count"] > 0
    inputs = analyst.get(f"/mrv/calculation-runs/{run['id']}/inputs").json()
    assert inputs["total"] == run["input_count"] and inputs["items"][0]["entity_type"] == "monitoring_record"
    assert result["baseline_value"] is not None and result["change_from_baseline"] is not None
    assert result["calculation_status"] == "calculated" and result["verification_status"] == "unverified"
    # Recalculating supersedes the unreviewed result instead of deleting it
    second = _calculate(analyst, programme_id, period, ind)["result"]
    db.expire_all()
    first = analyst.get(f"/mrv/results/{result['id']}").json()
    assert first["superseded_by_id"] == second["id"]
    assert analyst.post(f"/mrv/results/{result['id']}/submit-for-review", json={}).status_code == 422


def test_cannot_verify_without_review_and_separation_of_duties(api, db, programme_id) -> None:
    period, ind, reviewer = _ids(db)
    analyst, ver, admin = api.as_("analyst"), api.as_("verifier"), api.as_("admin")
    result = _calculate(analyst, programme_id, period, ind)["result"]
    task = analyst.post(f"/mrv/results/{result['id']}/submit-for-review",
                        json={"reviewer_id": str(reviewer.id), "note": "please review"}).json()
    assert task["status"] == "pending_review"
    # Verified is impossible before the review is complete
    r = ver.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "looks fine"})
    assert r.status_code == 422 and "before its review" in r.json()["error"]["message"]
    # Admin submits their own result and cannot verify it
    own = _calculate(admin, programme_id, period, ind)["result"]
    own_task = admin.post(f"/mrv/results/{own['id']}/submit-for-review", json={}).json()
    assert admin.post(f"/mrv/review-tasks/{own_task['id']}/mark-reviewed", json={}).status_code == 403
    # Conditional decisions are disabled by default policy
    assert ver.post(f"/mrv/review-tasks/{task['id']}/decision",
                    json={"decision": "conditional", "rationale": "needs follow up"}).status_code == 422


def test_findings_corrections_and_verification(api, db, programme_id) -> None:
    period, ind, reviewer = _ids(db)
    analyst, rev, ver = api.as_("analyst"), api.as_("reviewer"), api.as_("verifier")
    result = _calculate(analyst, programme_id, period, ind)["result"]
    task = analyst.post(f"/mrv/results/{result['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}).json()
    finding = rev.post(f"/mrv/review-tasks/{task['id']}/findings", json={
        "finding_type": "data_quality", "severity": "major", "description": "Sensor DEMO-DEV-002 flagged suspect",
        "corrective_action": "Exclude suspect readings"}).json()
    assert rev.post(f"/mrv/review-tasks/{task['id']}/request-correction", json={"note": "Fix sensor data"}).status_code == 200
    assert analyst.get(f"/mrv/results/{result['id']}").json()["review_status"] == "correction_requested"
    assert analyst.patch(f"/mrv/findings/{finding['id']}", json={"corrective_status": "resolved",
                                                                "resolution_note": "Excluded"}).status_code == 200
    assert analyst.patch(f"/mrv/findings/{finding['id']}", json={"corrective_status": "accepted"}).status_code == 403
    assert analyst.post(f"/mrv/review-tasks/{task['id']}/resubmit", json={"note": "fixed"}).json()["status"] == "pending_review"
    assert ver.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={"note": "ok"}).status_code == 403  # verifier ≠ reviewer
    assert rev.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "x" * 6}).status_code == 403
    assert rev.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={"note": "ok"}).json()["status"] == "reviewed"
    decision = ver.post(f"/mrv/review-tasks/{task['id']}/decision",
                        json={"decision": "verified", "rationale": "Corrections confirmed"}).json()
    assert decision["decision"] == "verified" and decision["version"] == 1
    final = analyst.get(f"/mrv/results/{result['id']}").json()
    assert (final["review_status"], final["verification_status"]) == ("reviewed", "verified")
    detail = rev.get(f"/mrv/review-tasks/{task['id']}").json()
    assert detail["status"] == "closed" and len(detail["comments"]) >= 2 and detail["decisions"]
    history = ver.get(f"/mrv/entity-history/indicator_result/{result['id']}").json()
    assert [h["action"] for h in history][:2] == ["calculate", "submit_for_review"]
    assert history[-1]["action"] == "verification_decision"


def test_open_major_finding_blocks_verification(api, db, programme_id) -> None:
    period, ind, reviewer = _ids(db)
    analyst, rev, ver = api.as_("analyst"), api.as_("reviewer"), api.as_("verifier")
    result = _calculate(analyst, programme_id, period, ind)["result"]
    task = analyst.post(f"/mrv/results/{result['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}).json()
    rev.post(f"/mrv/review-tasks/{task['id']}/findings", json={"finding_type": "method", "severity": "critical",
                                                              "description": "Method not approved"})
    rev.post(f"/mrv/review-tasks/{task['id']}/mark-reviewed", json={})
    r = ver.post(f"/mrv/review-tasks/{task['id']}/decision", json={"decision": "verified", "rationale": "ok"})
    assert r.status_code == 422
    # Rejecting is still possible
    assert ver.post(f"/mrv/review-tasks/{task['id']}/decision",
                    json={"decision": "not_verified", "rationale": "Method pending approval"}).status_code == 201


def test_baseline_and_monitoring_review(api, db, programme_id) -> None:
    _, ind, reviewer = _ids(db)
    analyst = api.as_("analyst")
    b = analyst.post("/baselines", json={"indicator_id": str(ind.id), "programme_id": str(programme_id), "value": 20,
                                         "unit": "%", "period_label": "2025", "method": "sampling", "source": "survey"}).json()
    assert analyst.post(f"/baselines/{b['id']}/submit-for-review", json={}).status_code == 422  # not validated yet
    assert analyst.post(f"/baselines/{b['id']}/submit").json()["validation_status"] == "valid"
    task = analyst.post(f"/baselines/{b['id']}/submit-for-review", json={"reviewer_id": str(reviewer.id)}).json()
    assert task["entity_type"] == "baseline_record"
    assert analyst.patch(f"/baselines/{b['id']}", json={"value": 21}).status_code == 409
    incomplete = analyst.post("/baselines", json={"indicator_id": str(ind.id), "programme_id": str(programme_id),
                                                  "value": 20, "unit": "%"}).json()
    out = analyst.post(f"/baselines/{incomplete['id']}/submit").json()
    assert out["validation_status"] == "rejected" and len(out["validation_messages"]) == 3


def test_scoped_calculation_and_results_query(api, db, programme_id) -> None:
    period, _, _ = _ids(db)
    from app.models import GeoUnit
    ind = db.scalar(select(IndicatorDefinition).where(IndicatorDefinition.code == "COV_FARMERS_ENROLLED"))
    ap0 = db.scalar(select(GeoUnit).where(GeoUnit.name == "Alluri Sitharama Raju"))
    analyst = api.as_("analyst")
    whole = _calculate(analyst, programme_id, period, ind)["result"]
    part = _calculate(analyst, programme_id, period, ind, scope_type="district", scope_geo_unit_id=str(ap0.id))["result"]
    assert 0 < float(part["value"]) < float(whole["value"])
    assert part["scope_label"] == "Alluri Sitharama Raju (district)"
    results = analyst.get("/indicators/results", params={"indicator_code": "COV_FARMERS_ENROLLED",
                                                          "scope_type": "district"}).json()
    assert any(r["id"] == part["id"] for r in results["items"])
    # A district-scoped field officer cannot run a programme-wide calculation
    assert api.as_("field").post("/mrv/calculations", json={}).status_code in (403, 422)


def test_indicator_versioning_and_approval(api, db) -> None:
    pa = api.as_("programme_admin")
    ind = pa.post("/indicators", json={"code": "TEST_IND", "name": "Test", "family": "Programme coverage", "unit": "farmers",
                                       "definition": "d", "method_code": "enrolled_farmers"}).json()
    assert ind["status"] == "draft" and ind["requires_confirmation"] is True
    assert pa.post(f"/indicators/{ind['id']}/status", json={"status": "approved"}).status_code == 422
    pa.post(f"/indicators/{ind['id']}/status", json={"status": "submitted"})
    assert pa.post(f"/indicators/{ind['id']}/status", json={"status": "approved"}).status_code == 422  # no method reference
    pa.post(f"/indicators/{ind['id']}/status", json={"status": "draft"})
    pa.patch(f"/indicators/{ind['id']}", json={"method_reference": "Approved MRV plan §3", "eligibility": "Enrolled farmers",
                                               "baseline_method": "Registry count at start", "evidence_requirements": "Consent form",
                                               "verification_sample_approach": "5% random sample"})
    pa.post(f"/indicators/{ind['id']}/status", json={"status": "submitted"})
    approved = pa.post(f"/indicators/{ind['id']}/status", json={"status": "approved", "requires_confirmation": False}).json()
    assert approved["status"] == "approved"
    assert pa.patch(f"/indicators/{ind['id']}", json={"name": "x"}).status_code == 409
    v2 = pa.post(f"/indicators/{ind['id']}/new-version").json()
    assert v2["version"] == 2 and v2["status"] == "draft"
    assert [v["version"] for v in pa.get(f"/indicators/{ind['id']}/versions").json()] == [2, 1]
    assert pa.post("/indicators", json={"code": "TEST_IND", "name": "dup", "family": "x", "unit": "x",
                                        "definition": "d"}).status_code == 409
