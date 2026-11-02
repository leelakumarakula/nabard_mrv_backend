import pytest


def test_liveness(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Request-ID")


@pytest.mark.db
def test_database_reachable(client) -> None:
    response = client.get("/api/v1/health/db")
    assert response.status_code == 200
    assert response.json()["database"] == "reachable"


def test_request_id_is_echoed(client) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"


def test_openapi_documents_integration_contracts(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/integrations/{source_code}/measurements", "/api/v1/integrations/{source_code}/advisories",
                 "/api/v1/integrations/{source_code}/observations", "/api/v1/integrations/{source_code}/sync-runs",
                 "/api/v1/integrations/{source_code}/replay", "/api/v1/indicators/results", "/api/v1/reports/programme"):
        assert path in paths
