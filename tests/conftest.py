import pytest
from fastapi.testclient import TestClient

from backend.database import configure_database
from backend.main import app
from backend.services.controller_service import controller_runtime
from backend.services.simulation_service import simulation_control


@pytest.fixture
def client(tmp_path):
    database_file = tmp_path / "test_traffic.sqlite3"
    controller_runtime.reset()
    simulation_control.reset()
    configure_database("sqlite:///" + str(database_file))
    with TestClient(app) as test_client:
        yield test_client
