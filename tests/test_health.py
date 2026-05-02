from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "homestay-night-concierge",
    }


def test_tenant_health_returns_safe_fields() -> None:
    client = TestClient(app)

    response = client.get("/health/tenant/zhen123-house")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "tenant_slug": "zhen123-house",
        "timezone": "Asia/Taipei",
        "default_language": "zh-TW",
    }


def test_tenant_health_returns_404_for_missing_tenant() -> None:
    client = TestClient(app)

    response = client.get("/health/tenant/missing-tenant")

    assert response.status_code == 404
