from tests.conftest import TEST_PASSWORD


def test_login_success_and_me(api) -> None:
    me = api.as_("analyst").get("/auth/me").json()
    assert me["email"] == "mrv.analyst@demo.local"
    assert "mrv.calculate" in me["permissions"]
    assert me["all_scope"] is True


def test_login_failure(client) -> None:
    r = client.post("/api/v1/auth/login", data={"username": "admin@demo.local", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"
    assert r.json()["request_id"]


def test_unauthenticated_requests_are_rejected(client) -> None:
    for url in ("/api/v1/farmers", "/api/v1/indicators", "/api/v1/integrations/sources", "/api/v1/admin/users"):
        assert client.get(url).status_code == 401


def test_permission_enforced_on_backend(api) -> None:
    assert api.as_("field").get("/admin/users").status_code == 403
    assert api.as_("researcher").get("/farmers").status_code == 403
    assert api.as_("farmer").get("/indicators").status_code == 403
    assert api.as_("verifier").post("/mrv/calculations", json={}).status_code in (403, 422)


def test_password_reset_flow(client) -> None:
    r = client.post("/api/v1/auth/forgot-password", json={"email": "farmer@demo.local"})
    token = r.json()["dev_reset_token"]
    assert token
    r = client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "BrandNewPass-2026"})
    assert r.status_code == 200
    assert client.post("/api/v1/auth/login", data={"username": "farmer@demo.local", "password": TEST_PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", data={"username": "farmer@demo.local",
                                                   "password": "BrandNewPass-2026"}).status_code == 200
    # Token is single-use
    assert client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "AnotherPass-2026"}).status_code == 400


def test_forgot_password_does_not_reveal_accounts(client) -> None:
    r = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@demo.local"})
    assert r.status_code == 200 and r.json()["dev_reset_token"] is None


def test_admin_creates_scoped_user(api, db) -> None:
    admin = api.as_("admin")
    geo = admin.get("/geo-units", params={"unit_type": "state"}).json()
    karnataka = next(g for g in geo if g["name"] == "Andhra Pradesh")
    r = admin.post("/admin/users", json={"email": "new.officer@demo.local", "full_name": "New Officer",
                                         "password": "Officer-Pass-2026", "role_codes": ["field_officer"],
                                         "scopes": [{"geo_unit_id": karnataka["id"]}]})
    assert r.status_code == 201, r.text
    assert r.json()["must_change_password"] is True
    audit = admin.get("/admin/audit-events", params={"entity_type": "user", "entity_id": r.json()["id"]}).json()
    assert audit["items"][0]["action"] == "create"


def test_only_super_admin_grants_super_admin(api) -> None:
    r = api.as_("programme_admin").post("/admin/users", json={
        "email": "x@demo.local", "full_name": "X", "password": "Some-Pass-2026", "role_codes": ["super_admin"]})
    assert r.status_code == 403


def test_role_permissions_are_configurable(api) -> None:
    admin = api.as_("admin")
    role = next(r for r in admin.get("/admin/roles").json() if r["code"] == "research_partner")
    r = admin.put(f"/admin/roles/{role['id']}/permissions", json={"permissions": role["permissions"] + ["farmer.read"]})
    assert r.status_code == 200 and "farmer.read" in r.json()["permissions"]
    assert admin.put(f"/admin/roles/{role['id']}/permissions", json={"permissions": ["not.real"]}).status_code == 422
