def test_root_and_health(client):
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["status"] == "ok"
    assert client.get("/api/health").json() == {"status": "healthy"}


def test_status_does_not_claim_missing_simulation_is_connected(client):
    result = client.get("/api/status")
    assert result.status_code == 200
    assert result.json()["simulation_connected"] is False
    assert result.json()["last_data_update"] is None


def test_docs_and_openapi_available(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
