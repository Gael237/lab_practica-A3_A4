"""Autenticación del servicio (Laboratorio A3+A4 · caso guía).

Registro, login y token, sin librerías externas. Requisitos mínimos de la A3:
  1. Contraseñas con hash y sal (PBKDF2-HMAC-SHA256), nunca texto plano ni MD5.
  2. Token con expiración de 15 minutos o menos.
  3. El MISMO mensaje para usuario inexistente y contraseña incorrecta.
  4. Comparación en tiempo constante (hmac.compare_digest).
  5. Bloqueo temporal tras intentos fallidos.
  6. La bitácora de intentos NO guarda la contraseña.

Las piezas marcadas TODO(LAB) las completamos juntos en el laboratorio con ayuda del asistente de IA.
El secreto para firmar tokens se lee de TOKEN_SECRET en .env (nunca en el código).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass

ITERACIONES = 200_000
VIGENCIA_S = int(os.getenv("TOKEN_VIGENCIA_S", str(15 * 60)))
MAX_FALLOS, VENTANA_FALLOS_S = 5, 15 * 60
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MENSAJE_CREDENCIALES = "Correo o contraseña incorrectos"


def _secreto() -> bytes:
    s = os.getenv("TOKEN_SECRET", "")
    if len(s) < 32:
        raise RuntimeError("Define TOKEN_SECRET en .env con al menos 32 caracteres (python -c \"import secrets;print(secrets.token_hex(32))\")")
    return s.encode()


class ErrorAuth(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status = status


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_contrasena(contrasena: str, sal: bytes | None = None) -> str:
    """Devuelve 'pbkdf2$<iteraciones>$<sal>$<hash>' (todo en base64url)."""
    sal_bytes = sal if sal is not None else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal_bytes, ITERACIONES)
    return f"pbkdf2${ITERACIONES}${_b64(sal_bytes)}${_b64(dk)}"


def verificar_contrasena(contrasena: str, guardado: str) -> bool:
    """Recalcula el hash con la misma sal y compara en TIEMPO CONSTANTE."""
    try:
        algoritmo, iter_str, sal_b64, hash_b64 = guardado.split("$")
        if algoritmo != "pbkdf2":
            return False
        iteraciones = int(iter_str)
        sal_bytes = _unb64(sal_b64)
        hash_esperado = _unb64(hash_b64)
    except Exception:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal_bytes, iteraciones)
    return hmac.compare_digest(dk, hash_esperado)


def emitir_token(usuario: str, ahora: float | None = None) -> str:
    """Token 'payload.firma': payload = {sub, iat, exp} en base64url; firma = HMAC-SHA256 con TOKEN_SECRET."""
    ts = int(ahora if ahora is not None else time.time())
    datos = {
        "sub": usuario,
        "iat": ts,
        "exp": ts + VIGENCIA_S,
    }
    payload_b64 = _b64(json.dumps(datos, separators=(",", ":")).encode("utf-8"))
    firma = hmac.new(_secreto(), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64(firma)}"


def validar_token(token: str, ahora: float | None = None) -> str:
    """Devuelve el usuario si el token es válido; si no, ErrorAuth(401). Revisa formato, firma y expiración."""
    if not token or "." not in token:
        raise ErrorAuth(401, "Token inválido o mal formado")
    partes = token.split(".")
    if len(partes) != 2:
        raise ErrorAuth(401, "Token inválido o mal formado")

    payload_b64, firma_b64 = partes
    try:
        firma_recibida = _unb64(firma_b64)
    except Exception:
        raise ErrorAuth(401, "Token inválido")

    firma_esperada = hmac.new(_secreto(), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(firma_recibida, firma_esperada):
        raise ErrorAuth(401, "Firma de token inválida")

    try:
        datos = json.loads(_unb64(payload_b64).decode("utf-8"))
    except Exception:
        raise ErrorAuth(401, "Payload de token inválido")

    ts = ahora if ahora is not None else time.time()
    if ts >= datos.get("exp", 0):
        raise ErrorAuth(401, "Token expirado")

    usuario = datos.get("sub")
    if not usuario or not isinstance(usuario, str):
        raise ErrorAuth(401, "Token sin sujeto válido")
    return usuario


@dataclass
class Autenticador:
    ruta_db: str = ":memory:"

    def __post_init__(self):
        self.db = sqlite3.connect(self.ruta_db, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS usuarios (email TEXT PRIMARY KEY, hash TEXT NOT NULL)")
        # Bitácora de intentos: correo, hora y resultado. NUNCA la contraseña.
        self.db.execute("CREATE TABLE IF NOT EXISTS intentos (email TEXT, ts REAL, ok INTEGER)")

    def registrar(self, email: str, contrasena: str) -> None:
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise ErrorAuth(422, "Correo inválido")
        if len(contrasena) < 8:
            raise ErrorAuth(422, "La contraseña debe tener al menos 8 caracteres")
        try:
            with self.db:
                self.db.execute("INSERT INTO usuarios VALUES (?, ?)", (email, hash_contrasena(contrasena)))
        except sqlite3.IntegrityError:
            raise ErrorAuth(409, "El correo ya está registrado")

    def login(self, email: str, contrasena: str, ahora: float | None = None) -> str:
        email, ahora = email.strip().lower(), ahora or time.time()
        limite_tiempo = ahora - VENTANA_FALLOS_S
       # 1. Contar fallos recientes en la ventana de tiempo
        cur = self.db.execute(
            "SELECT COUNT(*) FROM intentos WHERE email = ? AND ok = 0 AND ts >= ?",
            (email, limite_tiempo),
        )
        fallos = cur.fetchone()[0]
        if fallos >= MAX_FALLOS:
            raise ErrorAuth(429, "Demasiados intentos fallidos. Intenta más tarde.")

        # 2. Buscar el usuario y su hash guardado
        cur = self.db.execute("SELECT hash FROM usuarios WHERE email = ?", (email,))
        fila = cur.fetchone()

        # 3. Verificar contraseña (o ejecutar hash dummy para mitigar timing attacks si no existe)
        if fila is None:
            hash_contrasena("dummy_password_timing_mitigation", b"0123456789abcdef")
            valido = False
        else:
            valido = verificar_contrasena(contrasena, fila[0])

        # 4. Registrar el intento en bitácora SIN guardar la contraseña
        with self.db:
            self.db.execute(
                "INSERT INTO intentos (email, ts, ok) VALUES (?, ?, ?)",
                (email, ahora, 1 if valido else 0),
            )

        if not valido:
            raise ErrorAuth(401, MENSAJE_CREDENCIALES)

        return emitir_token(email, ahora=ahora)
