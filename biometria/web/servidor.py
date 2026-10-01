"""Servidor de JumeX Lens: interfaz web + API JSON sobre SistemaBiometrico.

    python -m biometria.web                     # http://127.0.0.1:8000 con config_lens.yaml
    python -m biometria.web --puerto 8080 --config config.yaml

API (las fotos se envían como cuerpo binario de la petición, sin multipart):

    GET  /api/estado                        extractor, umbrales, nº de personas e integridad
    GET  /api/personas                      [{id, muestras, foto}]
    GET  /api/caras/<clave>.jpg             retrato de una persona
    POST /api/analizar                      cuerpo: la foto (solo el pipeline, no toca la BD)
    POST /api/verificar?id=<id>             cuerpo: la foto
    POST /api/identificar                   cuerpo: la foto
    POST /api/inscribir?id=<id>             cuerpo: la foto
    DELETE /api/personas?id=<id>            borra a una persona (todas sus muestras)

Los POST admiten `&caja=x,y,ancho,alto` (píxeles de la foto) para marcar la
cara a mano cuando el detector no la encuentra o elige otra.

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
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from biometria.config import RAIZ_REPO, cargar_config
from biometria.errores import ErrorBiometrico, IdInvalido
from biometria.sistema import SistemaBiometrico
from biometria.web.analisis import Analisis, Galeria, a_data_url, analizar, retrato_de_analisis

CARPETA_ESTATICOS = Path(__file__).resolve().parent / "static"
CONFIG_POR_DEFECTO = RAIZ_REPO / "config_lens.yaml"
N_CANDIDATOS = 3
HOSTS_PERMITIDOS = {"127.0.0.1", "localhost", "[::1]"}


class Aplicacion:
    """Estado compartido del servidor. Un único lock: la demo es local y YuNet/SFace
    no garantizan ser seguros entre hilos."""

    def __init__(self, ruta_config: Path):
        self.cfg = cargar_config(ruta_config)
        self.sistema = SistemaBiometrico(self.cfg)
        self.sistema.detector  # carga YuNet ya: si falta el modelo, falla al arrancar
        self.galeria = Galeria(self.cfg.bd.ruta.parent / "caras")
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def url_foto(self, id_persona: str) -> str | None:
        if not self.galeria.existe(id_persona):
            return None
        return f"/api/caras/{self.galeria.clave(id_persona)}.jpg"

    def estado(self) -> dict:
        datos = {
            "extractor": self.sistema.extractor.nombre,
            "dimension": self.sistema.extractor.dimension,
            "umbrales": {"verificacion": self.cfg.umbrales.verificacion,
                         "identificacion": self.cfg.umbrales.identificacion},
            "agregacion": self.cfg.bd.agregacion,
        }
        try:
            agrupadas = self.sistema.bd.por_id()
            datos.update(integridad="ok", personas=len(agrupadas),
                         muestras=sum(len(t) for t in agrupadas.values()))
        except ErrorBiometrico as e:
            datos.update(integridad=e.codigo, mensaje=e.mensaje, personas=0, muestras=0)
        return datos

    def personas(self) -> dict:
        agrupadas = self.sistema.bd.por_id()
        return {"personas": [
            {"id": id_persona, "muestras": len(templates), "foto": self.url_foto(id_persona)}
            for id_persona, templates in sorted(agrupadas.items(), key=lambda p: p[0].casefold())
        ]}

    # ------------------------------------------------------------------ #
    def _respuesta_analisis(self, analisis: Analisis) -> dict:
        """Lo común a las tres operaciones: detecciones, cara alineada, template..."""
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
            respuesta["template"] = {"dimension": int(analisis.template.size),
                                     "valores": [round(float(v), 5) for v in analisis.template]}
        return respuesta

    def _error(self, respuesta: dict, error: ErrorBiometrico) -> dict:
        respuesta["ok"] = False
        respuesta["error"] = error.a_dict()
        return respuesta

    def operar(self, operacion: str, id_persona: str | None, foto: bytes, region=None) -> dict:
        with self.lock:
            analisis = analizar(self.sistema, foto, region)
            respuesta = self._respuesta_analisis(analisis)
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

    def eliminar(self, id_persona: str) -> dict:
        with self.lock:
            borradas = self.sistema.bd.eliminar(id_persona)
            self.galeria.eliminar(id_persona)
            return {"ok": True, "resultado": {"id": id_persona.strip(), "muestras_borradas": borradas}}

    def _verificar(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.verificar_t(id_persona, analisis.template)
        return {"id": r.id, "aceptado": r.aceptado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "foto": self.url_foto(r.id)}

    def _identificar(self, _id: None, analisis: Analisis) -> dict:
        r = self.sistema.identificar_t(analisis.template)
        candidatos = [{"id": i, "puntuacion": p, "foto": self.url_foto(i)}
                      for i, p in self.sistema.ranking(analisis.template, N_CANDIDATOS)]
        return {"id": r.id, "identificado": r.identificado, "puntuacion": r.puntuacion,
                "umbral": r.umbral, "foto": self.url_foto(r.id) if r.id else None,
                "candidatos": candidatos, "personas": len(self.sistema.bd.ids())}

    def _inscribir(self, id_persona: str, analisis: Analisis) -> dict:
        r = self.sistema.inscribir_t(id_persona, analisis.template)
        retrato_nuevo = self.galeria.guardar_de_analisis(r.id, analisis)
        return {"id": r.id, "muestras": r.muestras, "nueva_persona": r.muestras == 1,
                "retrato_nuevo": retrato_nuevo, "foto": self.url_foto(r.id)}


class Manejador(BaseHTTPRequestHandler):
    server_version = "JumeXLens/1.0"
    app: Aplicacion  # se asigna al crear el servidor

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
        nombre = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
        return nombre in HOSTS_PERMITIDOS

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
            if ruta.startswith("/api/caras/") and ruta.endswith(".jpg"):
                fichero = self.app.galeria.ruta_por_clave(ruta[len("/api/caras/"):-len(".jpg")])
                if fichero is None:
                    return self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", "No hay retrato")
                return self._enviar(HTTPStatus.OK, fichero.read_bytes(), "image/jpeg")
        except ErrorBiometrico as e:
            return self._json({"ok": False, "error": e.a_dict()}, HTTPStatus.CONFLICT)
        self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {ruta}")

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
        partes = urlsplit(self.path)
        if partes.path != "/api/personas":
            return self._fallo(HTTPStatus.NOT_FOUND, "no_encontrado", f"No existe {partes.path}")
        id_persona = (parse_qs(partes.query).get("id") or [""])[0]
        try:
            self._json(self.app.eliminar(id_persona))
        except ErrorBiometrico as e:
            self._json({"ok": False, "error": e.a_dict()},
                       HTTPStatus.NOT_FOUND if e.codigo == "id_no_inscrito" else HTTPStatus.CONFLICT)


def crear_servidor(ruta_config: Path, host: str = "127.0.0.1", puerto: int = 8000) -> ThreadingHTTPServer:
    app = Aplicacion(ruta_config)
    manejador = type("ManejadorLens", (Manejador,), {"app": app})
    servidor = ThreadingHTTPServer((host, puerto), manejador)
    servidor.daemon_threads = True
    return servidor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m biometria.web", description="JumeX Lens (demo web)")
    parser.add_argument("--config", type=Path, default=CONFIG_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=8000)
    parser.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    args = parser.parse_args(argv)

    try:
        servidor = crear_servidor(args.config, "127.0.0.1", args.puerto)
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
    print("Ctrl+C para parar.")
    if not args.no_abrir:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor parado.")
    finally:
        servidor.server_close()
    return 0
