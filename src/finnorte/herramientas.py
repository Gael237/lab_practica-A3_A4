"""Agente con herramientas (T10): el modelo PROPONE una acción en JSON; tu código decide y la ejecuta.

Funciona igual con cualquier proveedor porque no depende del "function calling" nativo:
el modelo responde
  {"accion": "usar", "herramienta": "nombre", "argumentos": {...}, "razon": "..."}
o
  {"accion": "responder", "respuesta": "...", "fuentes": [...]}

Reglas que el código hace cumplir (no el modelo):
- Solo herramientas registradas; argumentos validados contra su esquema.
- Herramientas marcadas `sensible=True` requieren confirmación humana. `confirmar(h, args)` devuelve
  True (aprobada: se ejecuta), False (rechazada: no se ejecuta) o None (PENDIENTE: la decisión llegará
  en otra solicitud, como en el servicio web; tampoco se ejecuta).
- Detector de repeticiones: la misma herramienta con los mismos argumentos no se ejecuta dos veces.
- Máximo de pasos para evitar bucles; todo queda en la bitácora.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

from . import llm, trazas


@dataclass
class Herramienta:
    nombre: str
    descripcion: str
    esquema: dict                      # JSON Schema de los argumentos
    funcion: Callable[..., Any]
    sensible: bool = False             # requiere confirmación humana


@dataclass
class Paso:
    n: int
    propuesta: dict
    resultado: Any = None
    estado: str = ""                   # ejecutada | rechazada | pendiente | invalida | repetida | respuesta


@dataclass
class ResultadoAgente:
    respuesta: str
    fuentes: list
    pasos: list[Paso] = field(default_factory=list)
    terminado: bool = True


def _catalogo(herrs: dict[str, Herramienta]) -> str:
    return "\n".join(f"- {h.nombre}: {h.descripcion}. Argumentos (JSON Schema): {json.dumps(h.esquema, ensure_ascii=False)}"
                     + (" [SENSIBLE: requiere confirmación]" if h.sensible else "") for h in herrs.values())


SISTEMA_AGENTE = """Eres un asistente de ingeniería de FinNorte que resuelve tareas en pasos.
En cada turno devuelve SOLO un JSON con una de dos formas:
{{"accion": "usar", "herramienta": "<nombre>", "argumentos": {{...}}, "razon": "<por qué>"}}
{{"accion": "responder", "respuesta": "<respuesta final>", "fuentes": ["<id>", ...]}}
Herramientas disponibles:
{catalogo}
No inventes herramientas ni resultados. Si una herramienta falla, decide si reintentar o responder con lo que tienes."""

def _extraer_json(texto: str) -> dict:
    """Extrae un objeto JSON de la respuesta del modelo (soporta bloques ```json)."""
    s = texto.strip()
    if s.startswith("```"):
        lineas = s.splitlines()
        if len(lineas) >= 3:
            s = "\n".join(lineas[1:-1]).strip()
    inicio, fin = s.find("{"), s.rfind("}")
    if inicio == -1 or fin == -1 or fin < inicio:
        raise ValueError("La respuesta del modelo no contiene un objeto JSON válido")
    return json.loads(s[inicio : fin + 1])

def ejecutar_agente(
    tarea: str,
    herramientas: list[Herramienta],
    confirmar: Callable[[Herramienta, dict], bool | None] | None = None,
    max_pasos: int = 5,
    contexto: str = "",
) -> ResultadoAgente:
    herrs = {h.nombre: h for h in herramientas}
    sistema = SISTEMA_AGENTE.format(catalogo=_catalogo(herrs))
    historial: list[dict] = []
    pasos: list[Paso] = []
    ejecutadas_previas: set[str] = set()
    mensaje = f"Tarea: {tarea}" + (f"\n\nContexto:\n{contexto}" if contexto else "")

    for n in range(1, max_pasos + 1):
        resp = llm.generar(mensaje, sistema=sistema, historial=historial, json=True)
        salida_llm = resp.texto
        historial.append({"rol": "user", "texto": mensaje})
        historial.append({"rol": "assistant", "texto": salida_llm})

        try:
            propuesta = _extraer_json(salida_llm)
        except Exception as exc:
            paso = Paso(n=n, propuesta={"raw": salida_llm}, resultado=str(exc), estado="invalida")
            pasos.append(paso)
            trazas.registrar("agente_paso", n=n, estado="invalida", error=str(exc))
            mensaje = "Error de formato: devuelve ÚNICAMENTE un objeto JSON válido según las instrucciones."
            continue

        accion = propuesta.get("accion")

        if accion == "responder":
            respuesta = str(propuesta.get("respuesta", ""))
            fuentes = propuesta.get("fuentes") or []
            if not isinstance(fuentes, list):
                fuentes = [fuentes]
            paso = Paso(n=n, propuesta=propuesta, resultado=respuesta, estado="respuesta")
            pasos.append(paso)
            trazas.registrar("agente_paso", n=n, estado="respuesta", fuentes=fuentes)
            return ResultadoAgente(respuesta=respuesta, fuentes=fuentes, pasos=pasos, terminado=True)

        if accion != "usar":
            paso = Paso(n=n, propuesta=propuesta, resultado="Acción desconocida", estado="invalida")
            pasos.append(paso)
            trazas.registrar("agente_paso", n=n, estado="invalida", accion=accion)
            mensaje = "Acción inválida. Usa 'usar' o 'responder' en formato JSON."
            continue

        nombre = propuesta.get("herramienta", "")
        args = propuesta.get("argumentos")
        if not isinstance(args, dict):
            args = {}

        # 1. Validar que la herramienta esté registrada
        if nombre not in herrs:
            err = f"Herramienta '{nombre}' no registrada"
            pasos.append(Paso(n=n, propuesta=propuesta, resultado=err, estado="invalida"))
            trazas.registrar("agente_paso", n=n, estado="invalida", herramienta=nombre, error=err)
            mensaje = f"Error: {err}. Elige solo entre las herramientas disponibles."
            continue

        h = herrs[nombre]

        # 2. Validar argumentos contra el JSON Schema de la herramienta
        try:
            jsonschema.validate(instance=args, schema=h.esquema)
        except jsonschema.ValidationError as exc:
            err = f"Argumentos inválidos para {nombre}: {exc.message}"
            pasos.append(Paso(n=n, propuesta=propuesta, resultado=err, estado="invalida"))
            trazas.registrar("agente_paso", n=n, estado="invalida", herramienta=nombre, error=err)
            mensaje = f"Error de esquema: {err}. Corrige los argumentos."
            continue

        # 3. Detector de repeticiones exactas
        clave_rep = f"{nombre}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if clave_rep in ejecutadas_previas:
            err = f"Repetición bloqueada: '{nombre}' ya fue invocada con los mismos argumentos"
            pasos.append(Paso(n=n, propuesta=propuesta, resultado=err, estado="repetida"))
            trazas.registrar("agente_paso", n=n, estado="repetida", herramienta=nombre)
            mensaje = f"Aviso: {err}. Usa los resultados previos o responde."
            continue

        # 4. Confirmación humana si la herramienta es sensible
        if h.sensible:
            decision = confirmar(h, args) if confirmar is not None else False
            if decision is None:
                pasos.append(Paso(n=n, propuesta=propuesta, resultado=None, estado="pendiente"))
                trazas.registrar("agente_paso", n=n, estado="pendiente", herramienta=nombre, argumentos=args)
                return ResultadoAgente(
                    respuesta=f"Acción '{nombre}' pendiente de confirmación humana.",
                    fuentes=[],
                    pasos=pasos,
                    terminado=False,
                )
            if decision is False:
                pasos.append(Paso(n=n, propuesta=propuesta, resultado="Rechazada por el usuario", estado="rechazada"))
                trazas.registrar("agente_paso", n=n, estado="rechazada", herramienta=nombre, argumentos=args)
                ejecutadas_previas.add(clave_rep)
                mensaje = f"El usuario RECHAZÓ ejecutar '{nombre}'. No vuelvas a intentarla; responde informando esto."
                continue

        # 5. Ejecutar herramienta
        try:
            resultado = h.funcion(**args)
            ejecutadas_previas.add(clave_rep)
            pasos.append(Paso(n=n, propuesta=propuesta, resultado=resultado, estado="ejecutada"))
            trazas.registrar("agente_paso", n=n, estado="ejecutada", herramienta=nombre, argumentos=args)
            mensaje = f"Resultado de {nombre}: {json.dumps(resultado, ensure_ascii=False, default=str)}"
        except Exception as exc:
            err = f"Error al ejecutar {nombre}: {exc}"
            pasos.append(Paso(n=n, propuesta=propuesta, resultado=err, estado="invalida"))
            trazas.registrar("agente_paso", n=n, estado="invalida", herramienta=nombre, error=str(exc))
            mensaje = f"{err}. Decide si reintentar o responder con lo que tienes."

    return ResultadoAgente(
        respuesta="Se alcanzó el máximo de pasos sin concluir la tarea.",
        fuentes=[],
        pasos=pasos,
        terminado=False,
    )