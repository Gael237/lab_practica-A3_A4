"""Pruebas base del servicio (S4). Desde el Laboratorio A3+A4 el servicio usa token Bearer en lugar de X-API-Key;
las pruebas completas están en tests/test_lab_producto.py."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_SECRET", "x" * 40)
    import api.main as m
    m.auth = m.Autenticador(str(tmp_path / "u.db"))
    m._ventanas.clear()
    return TestClient(m.app)


def test_salud(cliente):
    assert cliente.get("/salud").json()["estado"] == "ok"


def test_sin_token_401_problem_json(cliente):
    r = cliente.post("/v1/preguntas", json={"pregunta": "¿Cada cuánto se rotan los secretos?"})
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
