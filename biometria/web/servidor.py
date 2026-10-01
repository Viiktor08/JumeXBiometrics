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
import json
import math
import mimetypes
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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


class Aplicacion:
    """Estado compartido del servidor. Un único lock: la demo es local y YuNet/SFace
    no garantizan ser seguros entre hilos."""

    def __init__(self, ruta_config: Path):
        self.cfg = cargar_config(ruta_config)
        self.sistema = SistemaBiometrico(self.cfg)
        self.sistema.detector  # carga YuNet ya: si falta el modelo, falla al arrancar
        self.galeria = Galeria(self.cfg.bd.ruta.parent / "caras")
        self.lock = threading.Lock()
        try:
            self.galeria.migrar(self.sistema.bd.claves_por_id())
        except ErrorBiometrico:
            pass  # BD rota: /api/estado lo enseñará

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
    def estado(self) -> dict:
        datos = {
            "extractor": self.sistema.extractor.nombre,
            "dimension": self.sistema.extractor.dimension,
            "umbrales": {"verificacion": self.cfg.umbrales.verificacion,
                         "identificacion": self.cfg.umbrales.identificacion},
            "agregacion": self.cfg.bd.agregacion,
            "max_muestras": self.cfg.limites.max_muestras_por_id,
        }
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

    def operar(self, operacion: str, id_persona: str | None, foto: bytes, region=None) -> dict:
        with self.lock:
            analisis = analizar(self.sistema, foto, region)
            respuesta = self._respuesta_analisis(analisis)
            # Identificar con varias caras: se comprueba cada una (salvo cara marcada a mano)
            if operacion == "identificar" and region is None and len(analisis.detecciones) >= 2:
                return self._identificar_varias(analisis, respuesta)
            if analisis.error is not None:
                return self._error(respuesta, analisis.error)
            inicio = time.perf_counter()
            try:
                respuesta["resultado"] = getattr(self, f"_{operacion}")(id_persona, analisis)
            except ErrorBiometrico as e:
                return self._error(respuesta, e)
            respuesta["tiempos_ms"]["comparacion"] = round((time.perf_counter() - inicio) * 1000, 1)
            return respuesta

    def _analizar(self, _id: None, _analisis: Analisis) -> dict:
        return {}

    def _candidatos(self, template, n: int = N_CANDIDATOS) -> list[dict]:
        claves = self.sistema.bd.claves_por_id()
        return [{"id": i, "puntuacion": p, "foto": self.url_foto(i, claves.get(i, []))}
                for i, p in self.sistema.ranking(template, n)]

    def _verificar(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.verificar_t(id_persona, analisis.template)
        return {"id": r.id, "aceptado": r.aceptado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "foto": self.url_foto(r.id)}

    def _identificar(self, _id: None, analisis: Analisis) -> dict:
        r = self.sistema.identificar_t(analisis.template)
        return {"id": r.id, "identificado": r.identificado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "foto": self.url_foto(r.id) if r.id else None,
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
                candidatos = self._candidatos(cara.template)
                mejor = candidatos[0] if candidatos else None
                conocido = mejor is not None and mejor["puntuacion"] >= umbral
                item.update(estado="identificada" if conocido else "desconocida",
                            id=mejor["id"] if conocido else None,
                            puntuacion=mejor["puntuacion"] if mejor else None,
                            foto=mejor["foto"] if conocido else None, candidatos=candidatos)
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
            "multiple": True, "umbral": umbral, "personas": len(self.sistema.bd.ids()),
            "identificadas": sum(c["estado"] == "identificada" for c in lista),
            "desconocidas": sum(c["estado"] == "desconocida" for c in lista),
            "descartadas": sum(c["estado"] == "descartada" for c in lista),
        })
        respuesta["tiempos_ms"]["comparacion"] = round((time.perf_counter() - inicio) * 1000, 1)
        return respuesta

    def _inscribir(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.inscribir_t(id_persona, analisis.template)
        clave = self.sistema.bd.muestras_de(r.id)[-1][0]  # la recién añadida va al final
        self.galeria.guardar_muestra_de_analisis(r.id, clave, analisis)
        return {"id": r.id, "muestras": r.muestras, "nueva_persona": r.muestras == 1,
                "clave": clave, "foto": self.url_foto(r.id)}


def _estado_http(error: ErrorBiometrico) -> int:
    if error.codigo in ("id_no_inscrito", "muestra_no_encontrada"):
        return HTTPStatus.NOT_FOUND
    if error.codigo == "id_invalido":
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.CONFLICT


def _template_json(template) -> dict:
    return {"dimension": int(template.size), "valores": [round(float(v), 5) for v in template]}


class Manejador(BaseHTTPRequestHandler):
    server_version = "JumeXLens/1.0"
    app: Aplicacion  # se asigna al crear el servidor
    dominios_extra: tuple[str, ...] = ()  # además de localhost (túnel)

    # ------------------------------------------------------------------ #
    def _enviar(self, estado: int, cuerpo: bytes, tipo: str, cache: bool = False) -> None:
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def _json(self, datos: dict, estado: int = HTTPStatus.OK) -> None:
        self._enviar(estado, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def _fallo(self, estado: int, codigo: str, mensaje: str) -> None:
        self._json({"ok": False, "error": {"error": codigo, "mensaje": mensaje}}, estado)

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
                    return self._json(self.app.estado())
            if ruta == "/api/personas":
                with self.app.lock:
                    return self._json(self.app.personas())
            if ruta == "/api/persona":
                with self.app.lock:
                    return self._json(self.app.persona(self._parametro("id")))
            if ruta.startswith("/api/caras/"):
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
        # Edición de la BD (sin foto)
        if partes.path in ("/api/persona/principal", "/api/persona/renombrar"):
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
        self._json(self.app.operar(operacion, id_persona, foto, region))

    def do_DELETE(self) -> None:
        if not self._host_valido():
            return self._fallo(HTTPStatus.FORBIDDEN, "host_no_permitido", "Solo se admite localhost")
        if self.headers.get("X-JumeX-Lens") != "1":
            return self._fallo(HTTPStatus.FORBIDDEN, "cabecera_requerida", "Falta la cabecera X-JumeX-Lens")
        ruta = urlsplit(self.path).path
        try:
            if ruta == "/api/personas":
                return self._json(self.app.eliminar(self._parametro("id")))
            if ruta == "/api/muestras":
                return self._json(self.app.eliminar_muestra(self._parametro("id"), self._parametro("clave")))
        except ErrorBiometrico as e:
            return self._json({"ok": False, "error": e.a_dict()}, _estado_http(e))
        self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {ruta}")


def crear_servidor(ruta_config: Path, host: str = "127.0.0.1", puerto: int = 8000,
                   dominios_extra: tuple[str, ...] = ()) -> ThreadingHTTPServer:
    app = Aplicacion(ruta_config)
    manejador = type("ManejadorLens", (Manejador,), {"app": app, "dominios_extra": tuple(dominios_extra)})
    servidor = ThreadingHTTPServer((host, puerto), manejador)
    servidor.daemon_threads = True
    return servidor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m biometria.web", description="JumeX Lens (demo web)")
    parser.add_argument("--config", type=Path, default=CONFIG_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=8000)
    parser.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    parser.add_argument("--tunel", action="store_true",
                        help="publica la web con un túnel de Cloudflare (necesita cloudflared)")
    args = parser.parse_args(argv)

    if args.tunel and shutil.which("cloudflared") is None:
        print("No se encuentra cloudflared. En macOS: brew install cloudflared")
        return 1
    try:
        servidor = crear_servidor(args.config, "127.0.0.1", args.puerto,
                                  (DOMINIO_TUNEL,) if args.tunel else ())
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
                      "  Cualquiera con esta URL puede usar la web y editar la base de datos.\n", flush=True)
            elif "ERR" in linea and not avisado:
                print(f"[cloudflared] {linea.strip()}", flush=True)

    threading.Thread(target=leer, daemon=True).start()
    return proceso
