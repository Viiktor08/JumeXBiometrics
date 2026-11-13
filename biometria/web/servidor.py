"""Servidor de JumeX Lens: interfaz web + API JSON sobre SistemaBiometrico.

    python -m biometria.web                     # http://127.0.0.1:8000 con config_lens.yaml
    python -m biometria.web --puerto 8080 --config config.yaml

API (las fotos se envían como cuerpo binario de la petición, sin multipart):

    GET  /api/estado                        extractor, umbrales, nº de personas e integridad
    GET  /api/personas                      [{id, muestras, foto}]
    GET  /api/persona?id=<id>               ficha: muestras con su foto
    GET  /api/caras/<persona>/<muestra>.jpg foto de una muestra (o /api/caras/<persona>.jpg)
    POST /api/persona/principal?id=&clave=  elige la foto principal
    POST /api/persona/renombrar?id=&nuevo=  cambia el nombre
    DELETE /api/muestras?id=<id>&clave=<c>  borra una muestra
    POST /api/analizar                      cuerpo: la foto (solo el pipeline, no toca la BD)
    POST /api/verificar?id=<id>             cuerpo: la foto
    POST /api/identificar                   cuerpo: la foto
    POST /api/inscribir?id=<id>             cuerpo: la foto
    DELETE /api/personas?id=<id>            borra a una persona (todas sus muestras)

Los POST admiten `&caja=x,y,ancho,alto` (píxeles de la foto) para marcar la
cara a mano cuando el detector no la encuentra o elige otra.

Con --tunel se publica en una URL https://<algo>.trycloudflare.com mediante un
túnel rápido de Cloudflare (cloudflared): el servidor sigue en este equipo y
solo se acepta además ese dominio.

Solo escucha en 127.0.0.1. Además, para que otra web abierta en el navegador
no pueda usar la API (CSRF / DNS rebinding): se exige la cabecera
`X-JumeX-Lens: 1` en los POST (obliga a un preflight CORS que nunca se aprueba)
y se rechazan las peticiones cuyo Host no sea localhost.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

from biometria.config import RAIZ_REPO, cargar_config
from biometria.bd import validar_id
from biometria.errores import ErrorBiometrico, IdInvalido, MuestraNoEncontrada
from biometria.sistema import SistemaBiometrico
from biometria.web.analisis import (
    Analisis,
    Galeria,
    a_data_url,
    analizar,
    analizar_todas,
    retrato,
    retrato_de_analisis,
)

CARPETA_ESTATICOS = Path(__file__).resolve().parent / "static"
CONFIG_POR_DEFECTO = RAIZ_REPO / "config_lens.yaml"
N_CANDIDATOS = 3
LADO_RETRATO_GRUPO = 192
HOSTS_PERMITIDOS = {"127.0.0.1", "localhost", "[::1]"}
DOMINIO_TUNEL = "trycloudflare.com"
PATRON_URL_TUNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
VARIABLE_ADMIN = "BIOMETRIA_ADMIN_PASS"
COOKIE_SESION = "jumex_sesion"

# --- Endurecimiento de la web (THREAT_MODEL.md, paso 8) ---------------------
DURACION_SESION_S = 2 * 3600       # la sesión de admin caduca a las 2 h
MAX_FALLOS_PIN_IP = 5              # fallos de PIN por IP en la ventana -> bloqueo de esa IP
MAX_FALLOS_PIN_TOTAL = 20          # fallos de PIN de todo el mundo -> bloqueo general (IPs rotando)
VENTANA_PIN_S = 15 * 60
MAX_PETICIONES_IP = 60             # operaciones con foto por IP y minuto (cada una gasta CPU)
VENTANA_PETICIONES_S = 60
TIMEOUT_SOCKET_S = 20              # una conexión callada más tiempo se cierra (slowloris)
MAX_CONEXIONES = 32                # conexiones a la vez; las demás se cierran al momento
LONGITUD_MINIMA_PIN = 8
PINS_COMUNES = {"password", "contraseña", "admin", "administrador", "qwerty", "jumex", "jumexlens",
                "letmein", "iloveyou", "biometria", "abc123", "abcd1234", "qwertyui"}


def pin_debil(pin: str) -> str | None:
    """Motivo por el que un PIN es débil (None si es aceptable)."""
    if len(pin) < LONGITUD_MINIMA_PIN:
        return f"tiene menos de {LONGITUD_MINIMA_PIN} caracteres"
    if pin.casefold() in PINS_COMUNES:
        return "es una contraseña muy común"
    if len(set(pin)) <= 2:
        return "repite casi siempre el mismo carácter"
    if pin.isdigit() and (pin in "01234567890123456789" or pin in "98765432109876543210"):
        return "es una secuencia de números"
    return None


class Limitador:
    """Cuenta sucesos por clave en una ventana deslizante (en memoria, seguro entre hilos)."""

    def __init__(self, maximo: int, ventana_s: float):
        self.maximo, self.ventana = maximo, ventana_s
        self._marcas: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _limpiar(self, clave: str, ahora: float) -> deque[float]:
        if len(self._marcas) > 10_000:  # que el diccionario no crezca sin fin
            self._marcas = {k: m for k, m in self._marcas.items() if m and ahora - m[-1] <= self.ventana}
        marcas = self._marcas.setdefault(clave, deque())
        while marcas and ahora - marcas[0] > self.ventana:
            marcas.popleft()
        return marcas

    def espera(self, clave: str) -> int:
        """Segundos hasta que `clave` vuelva a tener cupo (0 = tiene cupo ya)."""
        ahora = time.monotonic()
        with self._lock:
            marcas = self._marcas.get(clave)
            if not marcas:
                return 0
            marcas = self._limpiar(clave, ahora)
            return 0 if len(marcas) < self.maximo else max(1, math.ceil(self.ventana - (ahora - marcas[0])))

    def anotar(self, clave: str) -> None:
        with self._lock:
            self._limpiar(clave, time.monotonic()).append(time.monotonic())

    def consumir(self, clave: str) -> int:
        """Anota un suceso si hay cupo y devuelve 0; si no, los segundos de espera."""
        ahora = time.monotonic()
        with self._lock:
            marcas = self._limpiar(clave, ahora)
            if len(marcas) >= self.maximo:
                return max(1, math.ceil(self.ventana - (ahora - marcas[0])))
            marcas.append(ahora)
            return 0


class Aplicacion:
    """Estado compartido del servidor. Un único lock: la demo es local y YuNet/ArcFace
    no garantizan ser seguros entre hilos.

    Dos roles (ver THREAT_MODEL.md, paso de roles):
      - usuario (cualquiera): verificar e identificar. En identificar solo ve el
        veredicto y el nombre, nunca la foto de la BD ni la lista de parecidos.
      - admin (con contraseña): además ve la base de datos, inscribe, borra y edita.
    """

    # Tope de personas que el público puede añadir mientras la inscripción está abierta
    MAX_INSCRITOS_ABIERTOS = 20

    def __init__(self, ruta_config: Path, admin_pass: str | None = None,
                 inscripcion_abierta_min: float | None = None):
        self.cfg = cargar_config(ruta_config)
        self.sistema = SistemaBiometrico(self.cfg)
        self.sistema.detector  # carga YuNet ya: si falta el modelo, falla al arrancar
        self.galeria = Galeria(self.cfg.bd.ruta.parent / "caras")
        self.lock = threading.Lock()
        # Contraseña de admin: argumento > variable de entorno > una aleatoria (se
        # imprime al arrancar). La comparación es en tiempo constante.
        self.admin_pass = admin_pass or os.environ.get(VARIABLE_ADMIN) or secrets.token_urlsafe(9)
        self.admin_pass_generada = not (admin_pass or os.environ.get(VARIABLE_ADMIN))
        self._sesiones: dict[str, float] = {}  # token de sesión admin -> caducidad (monotonic)
        self.fallos_pin_ip = Limitador(MAX_FALLOS_PIN_IP, VENTANA_PIN_S)
        self.fallos_pin_total = Limitador(MAX_FALLOS_PIN_TOTAL, VENTANA_PIN_S)
        self.peticiones_ip = Limitador(MAX_PETICIONES_IP, VENTANA_PETICIONES_S)
        # Sal de este proceso para la respuesta uniforme de verificar (ver _verificar_uniforme)
        self._sal = secrets.token_bytes(16)
        # Inscripción abierta TEMPORAL: cualquiera puede añadir personas NUEVAS (el "+")
        # hasta `_abierta_hasta`. Sin la opción de arranque no existe; caduca sola.
        self._abierta_hasta = (time.monotonic() + inscripcion_abierta_min * 60
                               if inscripcion_abierta_min and inscripcion_abierta_min > 0 else 0.0)
        self._inscritos_abiertos: set[str] = set()  # ids añadidos por el público en esta ejecución
        try:
            self.galeria.migrar(self.sistema.bd.claves_por_id())
        except ErrorBiometrico:
            pass  # BD rota: /api/estado lo enseñará

    # ------------------------------------------------------------------ #
    # Sesiones / roles
    # ------------------------------------------------------------------ #
    def login(self, contrasena: str, ip: str = "local") -> tuple[str | None, int]:
        """Comprueba el PIN. Devuelve (token de sesión o None, segundos de bloqueo).

        Contra la fuerza bruta: tras MAX_FALLOS_PIN_IP fallos desde una IP en la
        ventana, esa IP queda bloqueada; tras MAX_FALLOS_PIN_TOTAL fallos de
        cualquiera, se bloquea el login para todos (por si el atacante va
        cambiando de IP). Mientras dura el bloqueo, ni siquiera se mira el PIN:
        acertar en ese momento tampoco sirve."""
        espera = max(self.fallos_pin_ip.espera(ip), self.fallos_pin_total.espera("*"))
        if espera:
            return None, espera
        texto = contrasena.encode("utf-8")
        if not contrasena or not hmac.compare_digest(texto, self.admin_pass.encode("utf-8")):
            self.fallos_pin_ip.anotar(ip)
            self.fallos_pin_total.anotar("*")
            return None, 0
        token = secrets.token_urlsafe(24)
        with self.lock:
            ahora = time.monotonic()
            self._sesiones = {t: c for t, c in self._sesiones.items() if c > ahora}  # purga caducadas
            self._sesiones[token] = ahora + DURACION_SESION_S
        return token, 0

    def logout(self, token: str | None) -> None:
        with self.lock:
            self._sesiones.pop(token or "", None)

    def es_admin(self, token: str | None) -> bool:
        caducidad = self._sesiones.get(token or "")
        return caducidad is not None and caducidad > time.monotonic()

    # ------------------------------------------------------------------ #
    # Inscripción abierta temporal (el "+")
    # ------------------------------------------------------------------ #
    def segundos_inscripcion_abierta(self) -> int:
        """Segundos que le quedan a la ventana de inscripción abierta (0 = cerrada)."""
        return max(0, int(self._abierta_hasta - time.monotonic())) if self._abierta_hasta else 0

    def puede_inscribir_publico(self, id_persona: str) -> tuple[bool, str]:
        """¿Puede un NO admin inscribir a `id_persona` ahora? Devuelve (ok, motivo).

        Solo personas NUEVAS: añadir muestras a alguien que ya está inscrito (las
        49 reales) sigue siendo solo del admin, para no poder manipularlas."""
        if self.segundos_inscripcion_abierta() <= 0:
            return False, "requiere_admin"
        try:
            ya_existe = validar_id(id_persona) in self.sistema.bd.por_id()
        except ErrorBiometrico:
            return True, ""  # id inválido o BD rota: que el flujo normal dé el error concreto
        if ya_existe and validar_id(id_persona) not in self._inscritos_abiertos:
            return False, "nombre_ocupado"
        if (validar_id(id_persona) not in self._inscritos_abiertos
                and len(self._inscritos_abiertos) >= self.MAX_INSCRITOS_ABIERTOS):
            return False, "inscripcion_llena"
        return True, ""

    # ------------------------------------------------------------------ #
    # Fotos de la galería
    # ------------------------------------------------------------------ #
    def url_muestra(self, id_persona: str, clave_muestra: str) -> str | None:
        if not self.galeria.tiene_foto(id_persona, clave_muestra):
            return None
        return f"/api/caras/{self.galeria.clave(id_persona)}/{clave_muestra}.jpg"

    def url_foto(self, id_persona: str, claves: list[str] | None = None) -> str | None:
        """Foto principal: la muestra elegida, la primera con foto o el retrato antiguo."""
        if claves is None:
            claves = self.sistema.bd.claves_por_id().get(id_persona, [])
        principal = self.galeria.principal(id_persona, claves)
        if principal is not None:
            return self.url_muestra(id_persona, principal)
        if self.galeria.ruta_legado(id_persona).is_file():
            return f"/api/caras/{self.galeria.clave(id_persona)}.jpg"
        return None

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def estado(self, admin: bool = True) -> dict:
        datos = {
            "extractor": self.sistema.extractor.nombre,
            "dimension": self.sistema.extractor.dimension,
            "agregacion": self.cfg.bd.agregacion,
            "max_muestras": self.cfg.limites.max_muestras_por_id,
            "admin": admin,
            "inscripcion_abierta": self.segundos_inscripcion_abierta() > 0,
            "inscripcion_segundos": self.segundos_inscripcion_abierta(),
        }
        # Los umbrales y el recuento de personas son información del sistema que solo
        # ve el admin: a un usuario no le decimos cuánta gente hay ni con qué margen.
        if admin:
            datos["umbrales"] = {"verificacion": self.cfg.umbrales.verificacion,
                                 "identificacion": self.cfg.umbrales.identificacion,
                                 "parecido": self.cfg.umbrales.parecido}
            try:
                claves = self.sistema.bd.claves_por_id()
                datos.update(integridad="ok", personas=len(claves),
                             muestras=sum(len(c) for c in claves.values()))
            except ErrorBiometrico as e:
                datos.update(integridad=e.codigo, mensaje=e.mensaje, personas=0, muestras=0)
        return datos

    def personas(self) -> dict:
        claves = self.sistema.bd.claves_por_id()
        return {"personas": [
            {"id": id_persona, "muestras": len(c), "foto": self.url_foto(id_persona, c)}
            for id_persona, c in sorted(claves.items(), key=lambda p: p[0].casefold())
        ]}

    def persona(self, id_persona: str) -> dict:
        """Ficha de una persona: todas sus muestras con su foto (si la hay)."""
        id_persona = validar_id(id_persona)
        claves = [c for c, _ in self.sistema.bd.muestras_de(id_persona)]
        principal = self.galeria.principal(id_persona, claves)
        return {"ok": True, "id": id_persona, "foto": self.url_foto(id_persona, claves),
                "max_muestras": self.cfg.limites.max_muestras_por_id,
                "muestras": [{"clave": c, "foto": self.url_muestra(id_persona, c),
                              "principal": c == principal} for c in claves]}

    # ------------------------------------------------------------------ #
    # Edición de la BD
    # ------------------------------------------------------------------ #
    def eliminar(self, id_persona: str) -> dict:
        with self.lock:
            borradas = self.sistema.bd.eliminar(id_persona)
            self.galeria.eliminar(id_persona)
            return {"ok": True, "resultado": {"id": validar_id(id_persona), "muestras_borradas": borradas}}

    def eliminar_muestra(self, id_persona: str, clave: str) -> dict:
        with self.lock:
            quedan = self.sistema.bd.eliminar_muestra(id_persona, clave)
            if quedan == 0:
                self.galeria.eliminar(id_persona)
            else:
                self.galeria.eliminar_muestra(id_persona, clave)
            return {"ok": True, "resultado": {"id": validar_id(id_persona), "quedan": quedan}}

    def fijar_principal(self, id_persona: str, clave: str) -> dict:
        with self.lock:
            claves = [c for c, _ in self.sistema.bd.muestras_de(id_persona)]
            if clave not in claves or not self.galeria.tiene_foto(id_persona, clave):
                raise MuestraNoEncontrada("Esa muestra no existe o no tiene foto")
            self.galeria.fijar_principal(id_persona, clave)
            return self.persona(id_persona)

    def renombrar(self, id_persona: str, nuevo: str) -> dict:
        with self.lock:
            claves = self.sistema.bd.renombrar(id_persona, nuevo)
            self.galeria.renombrar(id_persona, nuevo, claves)
            return self.persona(nuevo)

    # ------------------------------------------------------------------ #
    # Operaciones con foto
    # ------------------------------------------------------------------ #
    def _respuesta_analisis(self, analisis: Analisis) -> dict:
        """Lo común a todas las operaciones: detecciones, cara alineada, template..."""
        respuesta: dict = {"ok": analisis.ok, "tiempos_ms": analisis.tiempos_ms,
                           "seleccion": analisis.seleccion,
                           "region": list(analisis.region) if analisis.region else None}
        if analisis.imagen is not None:
            alto, ancho = analisis.imagen.shape[:2]
            respuesta["imagen"] = {"ancho": ancho, "alto": alto}
        elegida = analisis.procesada.deteccion if analisis.procesada else None
        if elegida is None and analisis.detecciones and self.cfg.detector.varias_caras == "mayor":
            elegida = analisis.detecciones[0]
        respuesta["detecciones"] = [
            {"caja": list(map(int, d.caja)), "landmarks": d.landmarks.round(1).tolist(),
             "confianza": round(d.confianza, 4), "elegida": d is elegida}
            for d in analisis.detecciones
        ]
        if analisis.procesada is not None:
            respuesta["cara_alineada"] = a_data_url(analisis.procesada.cara)
            respuesta["nitidez"] = round(analisis.procesada.nitidez, 1)
            imagen_retrato = retrato_de_analisis(analisis)
            if imagen_retrato is not None:
                respuesta["retrato"] = a_data_url(imagen_retrato, ".jpg")
        if analisis.template is not None:
            respuesta["template"] = _template_json(analisis.template)
        return respuesta

    def _error(self, respuesta: dict, error: ErrorBiometrico) -> dict:
        respuesta["ok"] = False
        respuesta["error"] = error.a_dict()
        return respuesta

    def _verificar_uniforme(self, id_persona: str, analisis: Analisis) -> dict:
        """Verificar un nombre que NO existe, visto por un usuario: se responde como un
        rechazo normal, para que verificar no sirva para averiguar quién está inscrito.

        La puntuación es la de la foto frente a un vector "fantasma" que sale de un
        hash del nombre (con una sal de este proceso): parece la de un impostor
        cualquiera y es la misma cada vez que se prueba ese nombre."""
        semilla = hashlib.sha256(self._sal + validar_id(id_persona).casefold().encode("utf-8")).digest()
        fantasma = np.random.default_rng(int.from_bytes(semilla[:8], "big")).standard_normal(
            analisis.template.size).astype(np.float32)
        fantasma /= np.linalg.norm(fantasma)
        puntuacion = self.sistema._redondear(self.sistema.extractor.comparar(analisis.template, fantasma))
        return {"id": validar_id(id_persona), "aceptado": False, "puntuacion": puntuacion,
                "umbral": self.cfg.umbrales.verificacion, "foto": None}

    def operar(self, operacion: str, id_persona: str | None, foto: bytes,
               region=None, admin: bool = True) -> dict:
        with self.lock:
            analisis = analizar(self.sistema, foto, region)
            respuesta = self._respuesta_analisis(analisis)
            # Identificar con varias caras: se comprueba cada una (salvo cara marcada a mano)
            if operacion == "identificar" and region is None and len(analisis.detecciones) >= 2:
                return self._filtrar_rol(self._identificar_varias(analisis, respuesta), admin)
            if analisis.error is not None:
                return self._error(respuesta, analisis.error)
            inicio = time.perf_counter()
            try:
                respuesta["resultado"] = getattr(self, f"_{operacion}")(id_persona, analisis)
            except ErrorBiometrico as e:
                if not (operacion == "verificar" and not admin and e.codigo == "id_no_inscrito"):
                    return self._error(respuesta, e)
                respuesta["resultado"] = self._verificar_uniforme(id_persona, analisis)
            respuesta["tiempos_ms"]["comparacion"] = round((time.perf_counter() - inicio) * 1000, 1)
            return self._filtrar_rol(respuesta, admin)

    def _filtrar_rol(self, respuesta: dict, admin: bool) -> dict:
        """Recorta de la respuesta lo que un usuario no admin no debe ver: las fotos
        guardadas en la BD y la lista de personas más parecidas. El usuario sigue
        viendo el veredicto y el nombre identificado, pero nada que permita recorrer
        la base de datos por parecido. Ver THREAT_MODEL.md (roles)."""
        if admin:
            return respuesta
        r = respuesta.get("resultado")
        if isinstance(r, dict):
            r.pop("candidatos", None)
            r.pop("foto", None)           # foto de la persona de la BD (verificar/identificar)
        for cara in respuesta.get("caras", []):
            cara.pop("candidatos", None)
            cara.pop("foto", None)
        return respuesta

    def _analizar(self, _id: None, _analisis: Analisis) -> dict:
        return {}

    def _candidatos(self, template, n: int = N_CANDIDATOS) -> list[dict]:
        """Las `n` personas más parecidas, pero solo las que superan umbrales.parecido.

        Por debajo de ese umbral la puntuación es la de dos desconocidos cualesquiera:
        enseñarlas como "parecidas" solo confunde (sería ordenar ruido).
        """
        claves = self.sistema.bd.claves_por_id()
        minimo = self.cfg.umbrales.parecido
        return [{"id": i, "puntuacion": p, "foto": self.url_foto(i, claves.get(i, []))}
                for i, p in self.sistema.ranking(template, n) if p >= minimo]

    def _verificar(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.verificar_t(id_persona, analisis.template,
                                     firma=self.sistema.firmar(analisis.template), _contar=False)
        return {"id": r.id, "aceptado": r.aceptado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "foto": self.url_foto(r.id)}

    def _identificar(self, _id: None, analisis: Analisis) -> dict:
        r = self.sistema.identificar_t(analisis.template, firma=self.sistema.firmar(analisis.template),
                                       _contar=False)
        return {"id": r.id, "identificado": r.identificado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "parecido": self.cfg.umbrales.parecido,
                "ambiguo": r.ambiguo, "margen": self.cfg.defensa.margen_identificacion,
                "foto": self.url_foto(r.id) if r.id else None,
                "candidatos": self._candidatos(analisis.template),
                "personas": len(self.sistema.bd.ids())}

    def _identificar_varias(self, analisis: Analisis, respuesta: dict) -> dict:
        """Una foto con varias personas: identifica cada cara por separado."""
        inicio = time.perf_counter()
        caras = analizar_todas(self.sistema, analisis)
        respuesta["tiempos_ms"]["extraccion"] = round((time.perf_counter() - inicio) * 1000, 1)
        validas = [c for c in caras if c.template is not None]
        if not validas:
            return self._error(respuesta, caras[0].error)

        inicio = time.perf_counter()
        umbral = self.cfg.umbrales.identificacion
        lista = []
        for numero, cara in enumerate(caras, start=1):
            d = cara.deteccion
            item = {"numero": numero, "caja": list(map(int, d.caja)),
                    "landmarks": d.landmarks.round(1).tolist(), "confianza": round(d.confianza, 4),
                    "retrato": a_data_url(retrato(analisis.imagen, d.landmarks, LADO_RETRATO_GRUPO), ".jpg")}
            if cara.template is None:
                item.update(estado="descartada", error=cara.error.a_dict())
            else:
                # Misma regla que identificar_t: el mejor candidato, si supera el umbral
                r = self.sistema.identificar_t(cara.template, firma=self.sistema.firmar(cara.template),
                                               _contar=False)
                item.update(estado="identificada" if r.identificado else "desconocida",
                            id=r.id, puntuacion=r.puntuacion, ambiguo=r.ambiguo,
                            foto=self.url_foto(r.id) if r.id else None,
                            candidatos=self._candidatos(cara.template))
            lista.append(item)

        # El pipeline de la izquierda enseña la primera cara válida
        primera = validas[0]
        respuesta.setdefault("cara_alineada", a_data_url(primera.procesada.cara))
        respuesta.setdefault("nitidez", round(primera.procesada.nitidez, 1))
        respuesta.setdefault("template", _template_json(primera.template))
        respuesta.pop("error", None)
        for det in respuesta["detecciones"]:
            det["elegida"] = False
        respuesta.update(ok=True, multiple=True, caras=lista, resultado={
            "multiple": True, "umbral": umbral, "parecido": self.cfg.umbrales.parecido,
            "personas": len(self.sistema.bd.ids()),
            "identificadas": sum(c["estado"] == "identificada" for c in lista),
            "desconocidas": sum(c["estado"] == "desconocida" for c in lista),
            "descartadas": sum(c["estado"] == "descartada" for c in lista),
        })
        respuesta["tiempos_ms"]["comparacion"] = round((time.perf_counter() - inicio) * 1000, 1)
        return respuesta

    def _inscribir(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.inscribir_t(id_persona, analisis.template, firma=self.sistema.firmar(analisis.template))
        clave = self.sistema.bd.muestras_de(r.id)[-1][0]  # la recién añadida va al final
        self.galeria.guardar_muestra_de_analisis(r.id, clave, analisis)
        return {"id": r.id, "muestras": r.muestras, "nueva_persona": r.muestras == 1,
                "clave": clave, "foto": self.url_foto(r.id)}


def _estado_http(error: ErrorBiometrico) -> int:
    if error.codigo in ("id_no_inscrito", "muestra_no_encontrada"):
        return HTTPStatus.NOT_FOUND
    if error.codigo == "id_invalido":
        return HTTPStatus.BAD_REQUEST
    if error.codigo == "template_no_firmado":
        return HTTPStatus.FORBIDDEN
    if error.codigo == "demasiados_intentos":
        return HTTPStatus.TOO_MANY_REQUESTS
    return HTTPStatus.CONFLICT


def _template_json(template) -> dict:
    return {"dimension": int(template.size), "valores": [round(float(v), 5) for v in template]}


class Manejador(BaseHTTPRequestHandler):
    server_version = "JumeXLens/1.0"
    sys_version = ""  # no anunciar la versión de Python en la cabecera Server
    timeout = TIMEOUT_SOCKET_S  # socket: una conexión que no manda nada se cierra (slowloris)
    app: Aplicacion  # se asigna al crear el servidor
    dominios_extra: tuple[str, ...] = ()  # además de localhost (túnel)

    # ------------------------------------------------------------------ #
    def _enviar(self, estado: int, cuerpo: bytes, tipo: str, cache: bool = False,
                cookies: list[str] | None = None) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        # Cámara solo para la propia página (botón "Hacer una foto"); nada más
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _json(self, datos: dict, estado: int = HTTPStatus.OK, cookies: list[str] | None = None) -> None:
        self._enviar(estado, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8", cookies=cookies)

    def _fallo(self, estado: int, codigo: str, mensaje: str) -> None:
        self._json({"ok": False, "error": {"error": codigo, "mensaje": mensaje}}, estado)

    # ------------------------------------------------------------------ #
    # Rol (usuario por defecto; admin con sesión)
    # ------------------------------------------------------------------ #
    def _token(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_SESION)
        return morsel.value if morsel else None

    def _es_admin(self) -> bool:
        return self.app.es_admin(self._token())

    def _exige_admin(self) -> bool:
        """True si puede seguir; si no, responde 403 y devuelve False."""
        if self._es_admin():
            return True
        self._fallo(HTTPStatus.FORBIDDEN, "requiere_admin",
                    "Esta acción es solo para el administrador. Introduce el PIN de administrador.")
        return False

    def _ip(self) -> str:
        """IP del cliente. Con el túnel, todas las conexiones llegan desde cloudflared
        (127.0.0.1): la IP real la pone Cloudflare en CF-Connecting-IP (y la
        sobrescribe aunque el cliente la mande). Sin túnel, esa cabecera no se cree."""
        if self.dominios_extra and self.headers.get("CF-Connecting-IP"):
            return self.headers["CF-Connecting-IP"].strip()[:64]
        return self.client_address[0]

    def _por_tunel(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return any(host == d or host.endswith("." + d) for d in self.dominios_extra)

    def _host_valido(self) -> bool:
        host = self.headers.get("Host") or ""
        # "[::1]:8000" -> "[::1]"; "localhost:8000" -> "localhost"
        nombre = (host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]).lower()
        if nombre in HOSTS_PERMITIDOS:
            return True
        # Dominios extra (p. ej. el del túnel de Cloudflare): el propio o un subdominio
        return any(nombre == d or nombre.endswith("." + d) for d in self.dominios_extra)

    def log_message(self, formato: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {formato % args}")

    # ------------------------------------------------------------------ #
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_valido():
            return self._fallo(HTTPStatus.FORBIDDEN, "host_no_permitido", "Solo se admite localhost")
        ruta = urlsplit(self.path).path
        try:
            if ruta in ("/", "/index.html"):
                return self._estatico("index.html")
            if ruta.startswith("/static/"):
                return self._estatico(ruta.removeprefix("/static/"))
            if ruta == "/api/estado":
                with self.app.lock:
                    return self._json(self.app.estado(admin=self._es_admin()))
            # La base de datos (lista, fichas y fotos) es solo para el admin
            if ruta == "/api/personas":
                if not self._exige_admin():
                    return
                with self.app.lock:
                    return self._json(self.app.personas())
            if ruta == "/api/persona":
                if not self._exige_admin():
                    return
                with self.app.lock:
                    return self._json(self.app.persona(self._parametro("id")))
            if ruta.startswith("/api/caras/"):
                if not self._exige_admin():
                    return
                fichero = self.app.galeria.ruta_por_url(ruta[len("/api/caras/"):].split("/"))
                if fichero is None:
                    return self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", "No hay retrato")
                return self._enviar(HTTPStatus.OK, fichero.read_bytes(), "image/jpeg")
        except ErrorBiometrico as e:
            return self._json({"ok": False, "error": e.a_dict()}, _estado_http(e))
        self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {ruta}")

    def _parametro(self, nombre: str) -> str:
        return (parse_qs(urlsplit(self.path).query).get(nombre) or [""])[0]

    def _estatico(self, nombre: str) -> None:
        # Lista blanca: solo ficheros que existen directamente en static/ (sin subcarpetas ni '..')
        disponibles = {p.name: p for p in CARPETA_ESTATICOS.iterdir() if p.is_file()}
        if nombre not in disponibles:
            return self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", "Fichero no encontrado")
        tipo = mimetypes.guess_type(nombre)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript", "image/svg+xml"):
            tipo += "; charset=utf-8"
        self._enviar(HTTPStatus.OK, disponibles[nombre].read_bytes(), tipo)

    def do_POST(self) -> None:
        # Primero el cuerpo: responder un error sin leerlo corta la conexión mientras
        # el cliente aún envía (broken pipe). Lo que supera el límite no se lee nunca.
        try:
            longitud = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            longitud = -1
        maximo = self.app.cfg.limites.max_bytes_imagen
        if longitud > maximo or longitud < 0:
            self.close_connection = True
            return self._fallo(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "imagen_demasiado_grande",
                               f"La foto supera {maximo // (1024 * 1024)} MB")
        foto = self.rfile.read(longitud) if longitud else b""

        if not self._host_valido():
            return self._fallo(HTTPStatus.FORBIDDEN, "host_no_permitido", "Solo se admite localhost")
        if self.headers.get("X-JumeX-Lens") != "1":
            return self._fallo(HTTPStatus.FORBIDDEN, "cabecera_requerida", "Falta la cabecera X-JumeX-Lens")
        partes = urlsplit(self.path)
        # Entrar / salir del modo administrador (el cuerpo es el PIN, no una foto)
        if partes.path == "/api/login":
            token, espera = self.app.login(foto.decode("utf-8", "ignore").strip(), self._ip())
            if espera:
                self.log_message("login bloqueado para %s (%ss)", self._ip(), espera)
                return self._fallo(HTTPStatus.TOO_MANY_REQUESTS, "pin_bloqueado",
                                   f"Demasiados PIN incorrectos. Login bloqueado {math.ceil(espera / 60)} min.")
            if token is None:
                self.log_message("PIN incorrecto desde %s", self._ip())
                return self._fallo(HTTPStatus.UNAUTHORIZED, "pin_incorrecto", "PIN de administrador incorrecto")
            # HttpOnly: la sesión no es accesible desde JavaScript (anti-XSS).
            # SameSite=Strict + la cabecera X-JumeX-Lens ya cubren el CSRF.
            # Max-Age: caduca sola. Secure: por el túnel (HTTPS) no viaja nunca en claro.
            cookie = (f"{COOKIE_SESION}={token}; Path=/; HttpOnly; SameSite=Strict; "
                      f"Max-Age={DURACION_SESION_S}" + ("; Secure" if self._por_tunel() else ""))
            return self._json({"ok": True, "admin": True}, cookies=[cookie])
        if partes.path == "/api/logout":
            self.app.logout(self._token())
            cookie = f"{COOKIE_SESION}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            return self._json({"ok": True, "admin": False}, cookies=[cookie])
        # Edición de la BD (sin foto): solo admin
        if partes.path in ("/api/persona/principal", "/api/persona/renombrar"):
            if not self._exige_admin():
                return
            try:
                if partes.path.endswith("principal"):
                    datos = self.app.fijar_principal(self._parametro("id"), self._parametro("clave"))
                else:
                    datos = self.app.renombrar(self._parametro("id"), self._parametro("nuevo"))
            except ErrorBiometrico as e:
                return self._json({"ok": False, "error": e.a_dict()}, _estado_http(e))
            return self._json(datos)
        operacion = partes.path.removeprefix("/api/")
        if operacion not in ("analizar", "verificar", "identificar", "inscribir"):
            return self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {partes.path}")
        consulta = parse_qs(partes.query)

        id_persona = None
        if operacion in ("verificar", "inscribir"):
            id_persona = (consulta.get("id") or [""])[0]
            if not id_persona.strip():
                return self._fallo(HTTPStatus.BAD_REQUEST, IdInvalido.codigo, "Escribe un nombre")
        region = None
        if consulta.get("caja"):
            try:
                region = tuple(float(v) for v in consulta["caja"][0].split(","))
                if len(region) != 4 or not all(math.isfinite(v) for v in region):
                    raise ValueError
            except ValueError:
                return self._fallo(HTTPStatus.BAD_REQUEST, "caja_invalida", "caja debe ser x,y,ancho,alto")
        if not foto:
            return self._fallo(HTTPStatus.BAD_REQUEST, "imagen_invalida", "No se ha recibido ninguna foto")
        es_admin = self._es_admin()
        # Cada operación con foto gasta CPU (detector + ArcFace): límite por IP para
        # que nadie sature el servidor ni barra la BD con miles de fotos. El admin no.
        if not es_admin:
            espera = self.app.peticiones_ip.consumir(self._ip())
            if espera:
                return self._fallo(HTTPStatus.TOO_MANY_REQUESTS, "demasiadas_peticiones",
                                   f"Demasiadas peticiones seguidas. Espera {espera} s.")
        # Inscribir (modificar la BD) es del admin. Un usuario solo puede mientras la
        # inscripción abierta temporal esté activa y SOLO para personas nuevas.
        if operacion == "inscribir" and not es_admin:
            permitido, motivo = self.app.puede_inscribir_publico(id_persona)
            if not permitido:
                mensajes = {
                    "requiere_admin": "Inscribir es solo del administrador. Introduce el PIN de administrador.",
                    "nombre_ocupado": "Ese nombre ya está en la base de datos. Elige otro: solo el administrador "
                                      "puede añadir fotos a personas que ya existen.",
                    "inscripcion_llena": "Se ha alcanzado el máximo de personas que se pueden añadir ahora.",
                }
                return self._fallo(HTTPStatus.FORBIDDEN, motivo, mensajes[motivo])
        respuesta = self.app.operar(operacion, id_persona, foto, region, admin=es_admin)
        if operacion == "inscribir" and not es_admin and respuesta.get("ok") and respuesta.get("resultado"):
            self.app._inscritos_abiertos.add(respuesta["resultado"]["id"])  # cuenta para el tope
        self._json(respuesta)

    def do_DELETE(self) -> None:
        if not self._host_valido():
            return self._fallo(HTTPStatus.FORBIDDEN, "host_no_permitido", "Solo se admite localhost")
        if self.headers.get("X-JumeX-Lens") != "1":
            return self._fallo(HTTPStatus.FORBIDDEN, "cabecera_requerida", "Falta la cabecera X-JumeX-Lens")
        if not self._exige_admin():  # borrar de la BD es solo del admin
            return
        ruta = urlsplit(self.path).path
        try:
            if ruta == "/api/personas":
                return self._json(self.app.eliminar(self._parametro("id")))
            if ruta == "/api/muestras":
                return self._json(self.app.eliminar_muestra(self._parametro("id"), self._parametro("clave")))
        except ErrorBiometrico as e:
            return self._json({"ok": False, "error": e.a_dict()}, _estado_http(e))
        self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {ruta}")


class ServidorLens(ThreadingHTTPServer):
    """ThreadingHTTPServer con tope de conexiones simultáneas: cada conexión es un hilo,
    y sin tope un atacante podría abrir miles (y quedarse callado) hasta tumbarlo."""

    daemon_threads = True

    def __init__(self, *args, max_conexiones: int = MAX_CONEXIONES, **kwargs):
        self._cupo = threading.BoundedSemaphore(max_conexiones)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._cupo.acquire(blocking=False):
            self.shutdown_request(request)  # lleno: se cierra sin atender
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._cupo.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._cupo.release()


def crear_servidor(ruta_config: Path, host: str = "127.0.0.1", puerto: int = 8000,
                   dominios_extra: tuple[str, ...] = (), admin_pass: str | None = None,
                   inscripcion_abierta_min: float | None = None) -> ThreadingHTTPServer:
    app = Aplicacion(ruta_config, admin_pass=admin_pass, inscripcion_abierta_min=inscripcion_abierta_min)
    manejador = type("ManejadorLens", (Manejador,), {"app": app, "dominios_extra": tuple(dominios_extra)})
    return ServidorLens((host, puerto), manejador)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m biometria.web", description="JumeX Lens (demo web)")
    parser.add_argument("--config", type=Path, default=CONFIG_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=8000)
    parser.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    parser.add_argument("--tunel", action="store_true",
                        help="publica la web con un túnel de Cloudflare (necesita cloudflared)")
    parser.add_argument("--inscripcion-abierta", type=float, nargs="?", const=60.0, default=None, metavar="MIN",
                        help="abre TEMPORALMENTE el '+' para que cualquiera añada personas nuevas durante "
                             "MIN minutos (60 si no se indica); luego se cierra solo. Sin esta opción no existe")
    parser.add_argument("--admin-pass", default=None,
                        help=f"PIN de administrador (si no, {VARIABLE_ADMIN} o uno aleatorio que se imprime)")
    args = parser.parse_args(argv)

    pin = args.admin_pass or os.environ.get(VARIABLE_ADMIN)
    motivo = pin_debil(pin) if pin else None
    if motivo and args.tunel:
        print(f"El PIN de administrador {motivo}: con --tunel la web es pública y se podría adivinar.\n"
              f"Usa uno de {LONGITUD_MINIMA_PIN}+ caracteres (o no pases ninguno y se genera uno aleatorio).")
        return 1
    if motivo:
        print(f"AVISO: el PIN de administrador {motivo}. Vale en local; NO lo uses con --tunel.")
    if args.tunel and shutil.which("cloudflared") is None:
        print("No se encuentra cloudflared. En macOS: brew install cloudflared")
        return 1
    try:
        servidor = crear_servidor(args.config, "127.0.0.1", args.puerto,
                                  (DOMINIO_TUNEL,) if args.tunel else (), admin_pass=args.admin_pass,
                                  inscripcion_abierta_min=args.inscripcion_abierta)
    except ErrorBiometrico as e:
        print(f"Error [{e.codigo}]: {e.mensaje}")
        return 1
    except OSError as e:
        print(f"No se pudo abrir el puerto {args.puerto}: {e}. Prueba con --puerto 8080")
        return 1

    app = servidor.RequestHandlerClass.app
    estado = app.estado()
    url = f"http://127.0.0.1:{args.puerto}"
    print(f"JumeX Lens en {url}  ·  extractor {estado['extractor']}  ·  "
          f"{estado['personas']} personas en {app.cfg.bd.ruta}")
    if args.inscripcion_abierta:
        print(f"\n  INSCRIPCIÓN ABIERTA durante {args.inscripcion_abierta:g} min: cualquiera puede añadir "
              f"personas NUEVAS con el '+' (máx. {app.MAX_INSCRITOS_ABIERTOS}). Después se cierra sola.")
    if app.admin_pass_generada:
        print(f"\n  PIN de administrador (para inscribir, borrar y ver la base de datos): {app.admin_pass}")
        print(f"  Para fijarlo tú: export {VARIABLE_ADMIN}=tu_pin  (o --admin-pass tu_pin)\n")
    else:
        print("  Rol usuario por defecto; el admin entra con su PIN desde la web.")
    if estado["personas"] == 0:
        print("La BD está vacía. Para cargar las 50 personas de LFW:\n"
              f"  python scripts/inscribir_csv.py data/lfw/personas.csv --config {args.config}")
    tunel = abrir_tunel(url) if args.tunel else None
    print("Ctrl+C para parar.")
    if not args.no_abrir:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor parado.")
    finally:
        servidor.server_close()
        if tunel is not None:
            tunel.terminate()
    return 0


def abrir_tunel(url_local: str) -> subprocess.Popen:
    """Arranca un túnel rápido de Cloudflare (sin cuenta) hacia el servidor local
    y escribe la URL pública https://<algo>.trycloudflare.com en cuanto la recibe.

    La URL cambia cada vez que se arranca y solo funciona mientras este proceso
    siga abierto.
    """
    proceso = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", url_local],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )

    def leer() -> None:
        avisado = False
        for linea in proceso.stderr:
            encontrada = PATRON_URL_TUNEL.search(linea)
            if encontrada and not avisado:
                avisado = True
                print(f"\n  URL pública (Cloudflare Tunnel): {encontrada.group(0)}\n"
                      "  Cualquiera con esta URL puede verificar e identificar; editar la BD exige el PIN.\n", flush=True)
            elif "ERR" in linea and not avisado:
                print(f"[cloudflared] {linea.strip()}", flush=True)

    threading.Thread(target=leer, daemon=True).start()
    return proceso
