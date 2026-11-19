"""Laboratorio A3+A4 · producto: servicio con token, agente con confirmación en dos pasos.
Corre sin red (proveedor fake). Cópiala y adáptala para tu variante (tests/test_a3_*.py y test_a4_*.py)."""
import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import pendiente  # noqa: E402
from finnorte import llm, trazas  # noqa: E402
from finnorte.guia_herramientas import TICKETS  # noqa: E402

CRED = {"email": "ana@finnorte.mx", "contrasena": "segura123"}


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_SECRET", "x" * 40)
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "u.db"))
    import api.main as m
    m.auth = m.Autenticador(str(tmp_path / "u.db"))
    m._asistente = None
    m._ventanas.clear()
    TICKETS.clear()
    return TestClient(m.app)


def _ok(r):
    """Si el servicio responde 501 (pieza pendiente: A1, Avance 1, LAB o A4) la prueba se omite en vez de fallar."""
    if r.status_code == 501:
        pytest.skip(r.json()["detail"])
    return r


def _token(c):
    _ok(c.post("/auth/registro", json=CRED))
    return {"Authorization": "Bearer " + _ok(c.post("/auth/login", json=CRED)).json()["token"]}


def _guion(*resps):
    it = iter(resps)
    return lambda p, s: json.dumps(next(it))


def test_salud(cliente):
    assert cliente.get("/salud").json()["estado"] == "ok"


def test_sin_token_401_problem_json(cliente):
    r = cliente.post("/v1/preguntas", json={"pregunta": "¿Cada cuánto se rotan los secretos?"})
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "detail", "trace_id"} <= set(r.json())


@pendiente("LAB")
def test_registro_login_y_pregunta(cliente):
    r = _ok(cliente.post("/v1/preguntas", json={"pregunta": "¿Cada cuánto se rotan los secretos?"}, headers=_token(cliente)))
    assert r.status_code == 200 and "fuentes" in r.json()


@pendiente("LAB")
def test_mismo_mensaje_usuario_inexistente_y_contrasena_mala(cliente):
    _ok(cliente.post("/auth/registro", json=CRED))
    a = cliente.post("/auth/login", json={**CRED, "contrasena": "mala12345"}).json()
    b = cliente.post("/auth/login", json={"email": "nadie@finnorte.mx", "contrasena": "segura123"}).json()
    assert a["status"] == b["status"] == 401 and a["detail"] == b["detail"]


@pendiente("LAB")
def test_token_alterado_y_expirado(cliente, monkeypatch):
    h = _token(cliente)
    alterado = {"Authorization": h["Authorization"][:-2] + "xx"}
    assert cliente.post("/v1/preguntas", json={"pregunta": "hola hola"}, headers=alterado).status_code == 401
    from api import auth
    viejo = auth.emitir_token(CRED["email"], ahora=time.time() - 16 * 60)
    r = cliente.post("/v1/preguntas", json={"pregunta": "hola hola"}, headers={"Authorization": f"Bearer {viejo}"})
    assert r.status_code == 401 and "expirado" in r.json()["detail"]


@pendiente("LAB")
def test_bloqueo_tras_5_intentos(cliente):
    _ok(cliente.post("/auth/registro", json=CRED))
    for _ in range(5):
        cliente.post("/auth/login", json={**CRED, "contrasena": "mala12345"})
    assert cliente.post("/auth/login", json=CRED).status_code == 429


@pendiente("LAB")
def test_contrasena_no_se_guarda_en_texto_plano(cliente):
    _ok(cliente.post("/auth/registro", json=CRED))
    import api.main as m
    guardado = m.auth.db.execute("SELECT hash FROM usuarios").fetchone()[0]
    assert CRED["contrasena"] not in guardado and guardado.startswith("pbkdf2$")


def test_entrada_invalida_422(cliente):
    r = cliente.post("/auth/registro", json={"email": "malo", "contrasena": "x"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")


# ───────────── agente con confirmación en dos pasos ─────────────
REVISAR = {"accion": "usar", "herramienta": "revisar_ventana_despliegue", "argumentos": {"fecha": "2026-11-10", "riesgo": "alto"}}
TICKET = {"accion": "usar", "herramienta": "crear_ticket_cambio",
          "argumentos": {"titulo": "Despliegue pagos-api", "riesgo": "alto", "fecha": "2026-11-10"}}
FIN = {"accion": "responder", "respuesta": "Queda pendiente tu confirmación", "fuentes": ["ing-03"]}


@pendiente("LAB")
def test_agente_deja_pendiente_y_rechazo_no_ejecuta(cliente):
    h = _token(cliente)
    llm.fake.responder = _guion(REVISAR, TICKET, FIN)
    r = _ok(cliente.post("/v1/agente", json={"tarea": "Despliega pagos-api el 2026-11-10 y crea el ticket"}, headers=h)).json()
    assert r["validador"]["ok"] and len(r["pendientes"]) == 1 and TICKETS == []
    p = r["pendientes"][0]
    assert p["argumentos"] == TICKET["argumentos"]
    out = cliente.post(f"/v1/agente/confirmar/{p['id']}", json={"aprobar": False}, headers=h).json()
    assert out["estado"] == "rechazada" and TICKETS == []
    assert any(e.get("estado") == "rechazada" for e in trazas.leer("agente"))


@pendiente("LAB")
def test_agente_confirmado_ejecuta_exactamente_lo_aprobado(cliente):
    h = _token(cliente)
    llm.fake.responder = _guion(REVISAR, TICKET, FIN)
    p = _ok(cliente.post("/v1/agente", json={"tarea": "Despliega pagos-api el 2026-11-10"}, headers=h)).json()["pendientes"][0]
    out = cliente.post(f"/v1/agente/confirmar/{p['id']}", json={"aprobar": True}, headers=h).json()
    assert out["estado"] == "ejecutada" and TICKETS[0]["fecha"] == "2026-11-10"
    # un solo uso
    assert cliente.post(f"/v1/agente/confirmar/{p['id']}", json={"aprobar": True}, headers=h).status_code == 404


@pendiente("LAB")
def test_validador_bloquea_ticket_sin_ventana_permitida(cliente):
    h = _token(cliente)
    viernes = {"accion": "usar", "herramienta": "revisar_ventana_despliegue", "argumentos": {"fecha": "2026-11-13", "riesgo": "alto"}}
    ticket = {**TICKET, "argumentos": {**TICKET["argumentos"], "fecha": "2026-11-13"}}
    llm.fake.responder = _guion(viernes, ticket, FIN)
    r = _ok(cliente.post("/v1/agente", json={"tarea": "Despliega el viernes 13"}, headers=h)).json()
    assert not r["validador"]["ok"] and r["pendientes"] == []


@pendiente("LAB")
def test_otro_usuario_no_puede_confirmar(cliente):
    h = _token(cliente)
    llm.fake.responder = _guion(REVISAR, TICKET, FIN)
    p = _ok(cliente.post("/v1/agente", json={"tarea": "Despliega pagos-api el 2026-11-10"}, headers=h)).json()["pendientes"][0]
    otro = {"email": "beto@finnorte.mx", "contrasena": "segura456"}
    cliente.post("/auth/registro", json=otro)
    h2 = {"Authorization": "Bearer " + cliente.post("/auth/login", json=otro).json()["token"]}
    assert cliente.post(f"/v1/agente/confirmar/{p['id']}", json={"aprobar": True}, headers=h2).status_code == 404
