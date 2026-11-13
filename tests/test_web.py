"""Tests del extractor SFace y de la demo web JumeX Lens (API y galería)."""

import re
import http.client
import json
import threading

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE
from biometria.config import RAIZ_REPO
from biometria.errores import ConfiguracionInvalida, IdInvalido, TemplateInvalido
from biometria.extractores import crear_extractor
from biometria.extractores.arcface import MODELO_POR_DEFECTO as MODELO_ARCFACE
from biometria.extractores.sface import MODELO_POR_DEFECTO, ExtractorSFace
from biometria.web.analisis import Galeria, analizar
from biometria.web.servidor import Aplicacion, crear_servidor
from tests.conftest import cfg_con

import cv2  # noqa: I001

hay_sface = pytest.mark.skipif(not (RAIZ_REPO / MODELO_POR_DEFECTO).is_file(),
                               reason="Falta el modelo SFace: python scripts/descargar_modelos.py")


# --- SFace -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def sface():
    if not (RAIZ_REPO / MODELO_POR_DEFECTO).is_file():
        pytest.skip("Falta el modelo SFace")
    return ExtractorSFace()


@hay_sface
def test_sface_template_unitario_de_128(sface, detector, astronauta):
    t = sface.extraer(detector.procesar(astronauta).cara)
    assert t.shape == (128,) and t.dtype == np.float32
    assert np.linalg.norm(t) == pytest.approx(1.0, abs=1e-5)
    assert sface.comparar(t, t) == pytest.approx(1.0, abs=1e-5)


@hay_sface
def test_sface_distingue_la_cara_de_una_textura(sface, detector, astronauta):
    cara = detector.procesar(astronauta).cara
    alto, ancho = astronauta.shape[:2]
    girada = cv2.warpAffine(astronauta, cv2.getRotationMatrix2D((ancho / 2, alto / 2), 10, 1.0),
                            (ancho, alto), borderMode=cv2.BORDER_REFLECT)
    ruido = np.random.default_rng(0).integers(0, 256, cara.shape, dtype=np.uint8)
    t = sface.extraer(cara)
    misma = sface.comparar(t, sface.extraer(detector.procesar(girada).cara))
    otra = sface.comparar(t, sface.extraer(ruido))
    assert misma > 0.85 > otra


@hay_sface
def test_sface_valida_la_norma(sface):
    with pytest.raises(TemplateInvalido, match="norma"):
        sface.validar_template(np.ones(128))
    with pytest.raises(TemplateInvalido):
        sface.validar_template(np.zeros(127))


@hay_sface
def test_sface_desde_config_y_tam_cara(cfg):
    assert crear_extractor(cfg_con(cfg, extractor={"nombre": "sface", "parametros": {}})).dimension == 128
    with pytest.raises(ConfiguracionInvalida, match="112"):
        crear_extractor(cfg_con(cfg, preprocesado={"tam_cara": (96, 96)},
                                extractor={"nombre": "sface", "parametros": {}}))


def test_sface_modelo_inexistente(cfg, tmp_path):
    with pytest.raises(ConfiguracionInvalida, match="no existe el modelo"):
        crear_extractor(cfg_con(cfg, extractor={"nombre": "sface",
                                                "parametros": {"modelo": str(tmp_path / "x.onnx")}}))


# --- Galería y análisis ------------------------------------------------------------

def test_galeria_usa_hashes_como_nombres(tmp_path):
    galeria = Galeria(tmp_path)
    assert galeria.carpeta_persona("Ana López") == galeria.carpeta_persona("  Ana López ")
    assert galeria.carpeta_persona("Ana López").parent == tmp_path
    with pytest.raises(IdInvalido):
        galeria.carpeta_persona("../../etc/passwd")
    with pytest.raises(ValueError):
        galeria.ruta_muestra("Ana", "../../clave")
    persona = galeria.clave("Ana")
    for partes in (["../x.jpg"], ["A" * 20 + ".jpg"], [persona, "../k.jpg"], [persona, "0" * 15 + ".jpg"],
                   [persona, "0" * 16 + ".png"], [persona, "a", "b.jpg"]):
        assert galeria.ruta_por_url(partes) is None


def test_galeria_principal_y_migracion(tmp_path):
    galeria = Galeria(tmp_path)
    negra = np.zeros((32, 32, 3), np.uint8)
    a, b = "a" * 16, "b" * 16
    assert galeria.principal("Ana", [a, b]) is None
    galeria.guardar_muestra("Ana", b, negra)
    assert galeria.principal("Ana", [a, b]) == b          # la primera con foto
    galeria.guardar_muestra("Ana", a, negra)
    assert galeria.principal("Ana", [a, b]) == a
    galeria.fijar_principal("Ana", b)
    assert galeria.principal("Ana", [a, b]) == b          # la elegida
    assert galeria.ruta_por_url([galeria.clave("Ana"), f"{b}.jpg"]) == galeria.ruta_muestra("Ana", b)
    # Retrato antiguo (uno por persona): solo se migra si hay una única muestra
    cv2.imwrite(str(galeria.ruta_legado("Luis")), negra)
    cv2.imwrite(str(galeria.ruta_legado("Eva")), negra)
    assert galeria.migrar({"Luis": ["c" * 16], "Eva": ["d" * 16, "e" * 16]}) == 1
    assert galeria.tiene_foto("Luis", "c" * 16) and not galeria.tiene_foto("Eva", "d" * 16)


def test_analizar_guarda_cada_paso(cfg, tmp_path, detector, ruta_astronauta, monkeypatch):
    from biometria.sistema import SistemaBiometrico
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    sistema = SistemaBiometrico(cfg_con(cfg, bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "k"}))
    a = analizar(sistema, ruta_astronauta.read_bytes())
    assert a.ok and len(a.detecciones) == 1
    assert set(a.tiempos_ms) == {"carga", "deteccion", "alineado", "extraccion"}
    malo = analizar(sistema, b"no es una imagen")
    assert not malo.ok and malo.error.codigo == "imagen_invalida" and malo.template is None


# --- Servidor ------------------------------------------------------------------------

@pytest.fixture
def config_web(tmp_path, monkeypatch, detector):
    """config.yaml del repo (ArcFace) con la BD en una carpeta temporal."""
    if not (RAIZ_REPO / MODELO_ARCFACE).is_file():
        pytest.skip("Falta el modelo ArcFace: python scripts/descargar_modelos.py")
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    texto = (RAIZ_REPO / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("ruta: data/bd.csv", f"ruta: {tmp_path / 'bd.csv'}")
    re_clave = f"ruta_clave: {tmp_path / 'k.key'}"
    texto = re.sub(r"ruta_clave: \S+", lambda _: re_clave, texto)
    texto = texto.replace("modelo: models/", f"modelo: {RAIZ_REPO / 'models'}/")
    config = tmp_path / "config.yaml"
    config.write_text(texto, encoding="utf-8")
    return config


PIN_ADMIN = "pin-de-prueba-1234"


@pytest.fixture
def servidor(config_web):
    """Servidor real en un puerto libre, con config ArcFace, BD temporal y PIN conocido."""
    srv = crear_servidor(config_web, "127.0.0.1", 0, admin_pass=PIN_ADMIN)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def cookie_admin(puerto, pin=PIN_ADMIN):
    """Inicia sesión de admin y devuelve la cookie para las siguientes peticiones."""
    _, r, resp = peticion(puerto, "POST", "/api/login", pin.encode())
    assert r["ok"], "el PIN de prueba debería ser correcto"
    return resp.getheader("Set-Cookie").split(";")[0]


@pytest.fixture
def admin(servidor):
    """Puerto de un servidor con sesión de admin ya abierta: (puerto, cabeceras)."""
    return servidor, {"Cookie": cookie_admin(servidor)}


def peticion(puerto, metodo, ruta, cuerpo=None, cabeceras=None):
    conexion = http.client.HTTPConnection("127.0.0.1", puerto, timeout=30)
    conexion.request(metodo, ruta, body=cuerpo, headers={"X-JumeX-Lens": "1", **(cabeceras or {})})
    r = conexion.getresponse()
    datos = r.read()
    conexion.close()
    tipo = r.getheader("Content-Type", "")
    return r.status, (json.loads(datos) if tipo.startswith("application/json") else datos), r


def test_web_sirve_la_interfaz(servidor):
    estado, cuerpo, r = peticion(servidor, "GET", "/")
    assert estado == 200 and b"JumeX Lens" in cuerpo
    assert b'id="boton-camara"' in cuerpo and b'id="camara-video"' in cuerpo
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    # La cámara solo para la propia página; micrófono y ubicación, para nadie
    assert r.getheader("Permissions-Policy") == "camera=(self), microphone=(), geolocation=()"
    assert peticion(servidor, "GET", "/static/app.js")[0] == 200
    assert peticion(servidor, "GET", "/static/../bd.py")[0] == 404
    assert peticion(servidor, "GET", "/static/no_existe.js")[0] == 404


def test_web_flujo_inscribir_verificar_identificar(admin, ruta_astronauta):
    servidor, cab = admin  # inscribir y ver la BD exige sesión de administrador
    foto = ruta_astronauta.read_bytes()
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=Eileen%20Collins", foto, cab)
    assert estado == 200 and r["ok"] and r["resultado"]["nueva_persona"]
    assert r["detecciones"][0]["elegida"] and r["cara_alineada"].startswith("data:image/png;base64,")
    assert r["template"]["dimension"] == 512

    _, personas, _ = peticion(servidor, "GET", "/api/personas", cabeceras=cab)
    [p] = personas["personas"]
    assert p["id"] == "Eileen Collins" and p["foto"].startswith("/api/caras/")
    estado, imagen, _ = peticion(servidor, "GET", p["foto"], cabeceras=cab)
    assert estado == 200 and imagen[:2] == b"\xff\xd8"  # JPEG

    # Verificar como admin: devuelve la foto de la BD
    _, r, _ = peticion(servidor, "POST", "/api/verificar?id=Eileen%20Collins", foto, cab)
    assert r["resultado"]["aceptado"] is True and r["resultado"]["foto"] == p["foto"]

    _, r, _ = peticion(servidor, "POST", "/api/identificar", foto, cab)
    assert r["resultado"]["id"] == "Eileen Collins"
    assert r["resultado"]["candidatos"][0]["id"] == "Eileen Collins"

    _, estado_sistema, _ = peticion(servidor, "GET", "/api/estado", cabeceras=cab)
    assert estado_sistema["personas"] == 1 and estado_sistema["integridad"] == "ok"


def test_web_errores_controlados(admin, ruta_astronauta):
    servidor, cab = admin
    foto = ruta_astronauta.read_bytes()
    _, r, _ = peticion(servidor, "POST", "/api/verificar?id=Nadie", foto, cab)
    assert r["ok"] is False and r["error"]["error"] == "id_no_inscrito"
    assert len(r["detecciones"]) == 1  # se enseña la detección aunque falle después
    _, r, _ = peticion(servidor, "POST", "/api/identificar", b"basura", cab)
    assert r["error"]["error"] == "imagen_invalida"
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=", foto, cab)
    assert estado == 400
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=%3D1%2B1", foto, cab)
    assert r["error"]["error"] == "id_invalido"


def test_web_protecciones(servidor, ruta_astronauta):
    foto = ruta_astronauta.read_bytes()
    # Sin la cabecera personalizada (lo que mandaría un formulario de otra web)
    conexion = http.client.HTTPConnection("127.0.0.1", servidor, timeout=30)
    conexion.request("POST", "/api/identificar", body=foto)
    assert conexion.getresponse().status == 403
    conexion.close()
    # Host distinto de localhost (DNS rebinding)
    assert peticion(servidor, "GET", "/api/estado", cabeceras={"Host": "evil.example"})[0] == 403
    assert peticion(servidor, "GET", "/api/estado", cabeceras={"Host": f"localhost:{servidor}"})[0] == 200
    # Cuerpo demasiado grande: se rechaza sin leerlo
    conexion = http.client.HTTPConnection("127.0.0.1", servidor, timeout=30)
    conexion.putrequest("POST", "/api/identificar")
    conexion.putheader("X-JumeX-Lens", "1")
    conexion.putheader("Content-Length", str(50 * 1024 * 1024))
    conexion.endheaders()
    assert conexion.getresponse().status == 413
    conexion.close()
    # Path traversal en las fotos: como admin (si no, daría 403 antes de llegar a la ruta)
    cab = {"Cookie": cookie_admin(servidor)}
    assert peticion(servidor, "GET", "/api/caras/../../k.key", cabeceras=cab)[0] == 404


def test_web_caja_manual(servidor, ruta_astronauta, astronauta, detector):
    foto = ruta_astronauta.read_bytes()
    x, y, w, h = detector.detectar(astronauta)[0].caja
    # Recuadro aproximado sobre la cara: se busca dentro y se alinea con sus puntos
    _, r, _ = peticion(servidor, "POST", f"/api/analizar?caja={x + 8},{y + 8},{w - 16},{h - 16}", foto)
    assert r["ok"] and r["seleccion"] == "manual" and r["detecciones"][0]["elegida"]
    assert "resultado" in r and r["resultado"] == {}
    # Recuadro sin cara: se usa tal cual, sin alinear
    _, r, _ = peticion(servidor, "POST", "/api/analizar?caja=0,300,120,120", foto)
    assert r["ok"] and r["seleccion"] == "manual_sin_puntos" and r["region"] == [0, 300, 120, 120]
    # Recuadros inválidos
    assert peticion(servidor, "POST", "/api/analizar?caja=1,2,3", foto)[0] == 400
    assert peticion(servidor, "POST", "/api/analizar?caja=nan,0,50,50", foto)[0] == 400
    _, r, _ = peticion(servidor, "POST", "/api/analizar?caja=5,5,10,10", foto)
    assert r["error"]["error"] == "cara_pequena"


def test_web_analizar_no_toca_la_bd(admin, ruta_astronauta):
    servidor, cab = admin
    peticion(servidor, "POST", "/api/analizar", ruta_astronauta.read_bytes(), cab)
    _, estado, _ = peticion(servidor, "GET", "/api/estado", cabeceras=cab)
    assert estado["personas"] == 0


def test_web_eliminar_persona(admin, ruta_astronauta):
    servidor, cab = admin
    foto = ruta_astronauta.read_bytes()
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto, cab)
    _, personas, _ = peticion(servidor, "GET", "/api/personas", cabeceras=cab)
    url_foto = personas["personas"][0]["foto"]
    estado, r, _ = peticion(servidor, "DELETE", "/api/personas?id=Eileen", cabeceras=cab)
    assert estado == 200 and r["resultado"]["muestras_borradas"] == 1
    assert peticion(servidor, "GET", "/api/personas", cabeceras=cab)[1]["personas"] == []
    assert peticion(servidor, "GET", url_foto, cabeceras=cab)[0] == 404  # el retrato también se borra
    assert peticion(servidor, "DELETE", "/api/personas?id=Eileen", cabeceras=cab)[0] == 404
    conexion = http.client.HTTPConnection("127.0.0.1", servidor, timeout=30)
    conexion.request("DELETE", "/api/personas?id=Eileen")  # sin cabecera
    assert conexion.getresponse().status == 403
    conexion.close()



def test_web_identificar_varias_personas(admin, astronauta):
    # Dos copias de la misma persona y una tercera cara que no está inscrita (girada en espejo
    # no vale: sería la misma persona). Basta con comprobar que cada cara recibe su resultado.
    servidor, cab = admin
    ok, foto_una = cv2.imencode(".png", astronauta)
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto_una.tobytes(), cab)
    ok, foto_dos = cv2.imencode(".png", np.hstack([astronauta, astronauta]))
    _, r, _ = peticion(servidor, "POST", "/api/identificar", foto_dos.tobytes(), cab)
    assert r["ok"] and r["multiple"] and len(r["caras"]) == 2
    assert [c["numero"] for c in r["caras"]] == [1, 2]
    assert r["caras"][0]["caja"][0] < r["caras"][1]["caja"][0]          # izquierda a derecha
    assert all(c["estado"] == "identificada" and c["id"] == "Eileen" for c in r["caras"])
    assert all(c["retrato"].startswith("data:image/jpeg") for c in r["caras"])
    assert r["resultado"]["identificadas"] == 2 and r["resultado"]["desconocidas"] == 0
    # Marcando una cara a mano se vuelve al modo de una sola cara
    x, y, w, h = r["caras"][1]["caja"]
    _, r, _ = peticion(servidor, "POST", f"/api/identificar?caja={x},{y},{w},{h}", foto_dos.tobytes(), cab)
    assert not r.get("multiple") and r["resultado"]["id"] == "Eileen"


def test_web_ficha_y_edicion_de_persona(admin, ruta_astronauta, astronauta):
    servidor, cab = admin
    foto = ruta_astronauta.read_bytes()
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto, cab)
    ok, girada = cv2.imencode(".png", cv2.flip(astronauta, 1))
    _, r, _ = peticion(servidor, "POST", "/api/inscribir?id=Eileen", girada.tobytes(), cab)
    assert r["resultado"]["muestras"] == 2 and len(r["resultado"]["clave"]) == 16

    _, ficha, _ = peticion(servidor, "GET", "/api/persona?id=Eileen", cabeceras=cab)
    assert [m["principal"] for m in ficha["muestras"]] == [True, False]
    primera, segunda = (m["clave"] for m in ficha["muestras"])
    assert all(peticion(servidor, "GET", m["foto"], cabeceras=cab)[0] == 200 for m in ficha["muestras"])

    # Elegir la segunda como principal
    _, ficha, _ = peticion(servidor, "POST", f"/api/persona/principal?id=Eileen&clave={segunda}", None, cab)
    assert ficha["foto"].endswith(f"{segunda}.jpg")
    assert peticion(servidor, "POST", "/api/persona/principal?id=Eileen&clave=" + "0" * 16, None, cab)[0] == 404

    # Borrar una muestra
    _, r, _ = peticion(servidor, "DELETE", f"/api/muestras?id=Eileen&clave={primera}", cabeceras=cab)
    assert r["resultado"]["quedan"] == 1
    assert peticion(servidor, "DELETE", f"/api/muestras?id=Eileen&clave={primera}", cabeceras=cab)[0] == 404

    # Renombrar: las fotos se mueven con la persona
    _, ficha, _ = peticion(servidor, "POST", "/api/persona/renombrar?id=Eileen&nuevo=Eileen%20Collins", None, cab)
    assert ficha["id"] == "Eileen Collins" and peticion(servidor, "GET", ficha["foto"], cabeceras=cab)[0] == 200
    assert peticion(servidor, "GET", "/api/persona?id=Eileen", cabeceras=cab)[0] == 404
    assert peticion(servidor, "POST", "/api/persona/renombrar?id=Eileen%20Collins&nuevo=%3Dmal", None, cab)[0] == 400

    # Borrar la última muestra elimina a la persona. (Con la BD cifrada, renombrar
    # vuelve a cifrar con el id nuevo y la clave de la muestra cambia: se toma de la ficha.)
    segunda = ficha["muestras"][0]["clave"]
    _, r, _ = peticion(servidor, "DELETE", f"/api/muestras?id=Eileen%20Collins&clave={segunda}", cabeceras=cab)
    assert r["resultado"]["quedan"] == 0
    assert peticion(servidor, "GET", "/api/personas", cabeceras=cab)[1]["personas"] == []


def test_web_dominio_del_tunel(tmp_path, monkeypatch, detector):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    texto = (RAIZ_REPO / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("ruta: data/bd.csv", f"ruta: {tmp_path / 'bd.csv'}")
    re_clave = f"ruta_clave: {tmp_path / 'k.key'}"
    texto = re.sub(r"ruta_clave: \S+", lambda _: re_clave, texto)
    texto = texto.replace("modelo: models/", f"modelo: {RAIZ_REPO / 'models'}/")
    (tmp_path / "config.yaml").write_text(texto, encoding="utf-8")
    srv = crear_servidor(tmp_path / "config.yaml", "127.0.0.1", 0, ("trycloudflare.com",))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    puerto = srv.server_address[1]
    try:
        for host, esperado in (("abc-def.trycloudflare.com", 200), ("ABC.TryCloudflare.com", 200),
                               ("localhost", 200), ("evil.com", 403), ("trycloudflare.com.evil.com", 403),
                               ("eviltrycloudflare.com", 403)):
            assert peticion(puerto, "GET", "/api/estado", cabeceras={"Host": host})[0] == esperado, host
    finally:
        srv.shutdown()
        srv.server_close()


# --- Los más parecidos -------------------------------------------------------------------

def _vector_al_azar(semilla, dimension=512):
    v = np.random.default_rng(semilla).standard_normal(dimension)
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_web_solo_lista_como_parecidos_a_los_que_superan_el_umbral(config_web, ruta_astronauta):
    app = Aplicacion(config_web)
    foto = ruta_astronauta.read_bytes()
    assert app.operar("inscribir", "Eileen Collins", foto)["ok"]
    # Personas "al azar": en 512 dimensiones, dos vectores al azar son casi
    # perpendiculares (similitud ~0): nunca deben salir como parecidas
    for i in range(5):
        v = _vector_al_azar(i)
        app.sistema.inscribir_t(f"Persona {i}", v, firma=app.sistema.firmar(v))

    r = app.operar("identificar", None, foto)["resultado"]
    assert r["identificado"] and r["id"] == "Eileen Collins"
    assert r["parecido"] == app.cfg.umbrales.parecido == 0.30
    assert [c["id"] for c in r["candidatos"]] == ["Eileen Collins"]

    # Sin Eileen en la BD: desconocida, sin candidatos, pero con la puntuación del mejor
    app.eliminar("Eileen Collins")
    r = app.operar("identificar", None, foto)["resultado"]
    assert not r["identificado"] and r["candidatos"] == []
    assert 0.0 <= r["puntuacion"] < app.cfg.umbrales.parecido


def test_web_estado_incluye_el_umbral_de_parecido(admin):
    servidor, cab = admin
    _, estado, _ = peticion(servidor, "GET", "/api/estado", cabeceras=cab)
    assert estado["extractor"] == "arcface" and estado["dimension"] == 512
    assert estado["umbrales"] == {"verificacion": 0.35, "identificacion": 0.40, "parecido": 0.30}


# --- Roles: usuario (por defecto) y administrador (con PIN) ---------------------------------

def test_login_logout_y_pin_incorrecto(servidor):
    # PIN correcto abre sesión; uno incorrecto, 401
    assert peticion(servidor, "POST", "/api/login", b"mal")[0] == 401
    estado, r, resp = peticion(servidor, "POST", "/api/login", PIN_ADMIN.encode())
    assert estado == 200 and r["admin"] is True
    cab = {"Cookie": resp.getheader("Set-Cookie").split(";")[0]}
    # Con la cookie se es admin; tras logout, deja de serlo
    assert peticion(servidor, "GET", "/api/estado", cabeceras=cab)[1]["admin"] is True
    peticion(servidor, "POST", "/api/logout", cabeceras=cab)
    assert peticion(servidor, "GET", "/api/estado", cabeceras=cab)[1]["admin"] is False


def test_usuario_no_puede_ver_ni_editar_la_bd(servidor, ruta_astronauta):
    """Sin sesión (rol usuario) la base de datos no se ve ni se toca."""
    foto = ruta_astronauta.read_bytes()
    # Estado de usuario: sin umbrales ni recuento de personas
    _, estado, _ = peticion(servidor, "GET", "/api/estado")
    assert estado["admin"] is False and "umbrales" not in estado and "personas" not in estado
    # Todo lo que toca la BD -> 403
    assert peticion(servidor, "GET", "/api/personas")[0] == 403
    assert peticion(servidor, "GET", "/api/persona?id=X")[0] == 403
    assert peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto)[0] == 403
    assert peticion(servidor, "DELETE", "/api/personas?id=Eileen")[0] == 403
    assert peticion(servidor, "POST", "/api/persona/renombrar?id=a&nuevo=b")[0] == 403


def test_usuario_identifica_pero_sin_foto_ni_candidatos(servidor, ruta_astronauta):
    """El usuario ve el veredicto y el nombre, pero no la foto de la BD ni los parecidos."""
    cab = {"Cookie": cookie_admin(servidor)}
    foto = ruta_astronauta.read_bytes()
    peticion(servidor, "POST", "/api/inscribir?id=Eileen%20Collins", foto, cab)  # inscribe el admin

    # Como usuario (sin cookie)
    _, r, _ = peticion(servidor, "POST", "/api/identificar", foto)
    res = r["resultado"]
    assert res["id"] == "Eileen Collins" and res["identificado"] is True
    assert res["puntuacion"] is not None          # el veredicto sí
    assert "foto" not in res                        # pero no la foto de la BD
    assert "candidatos" not in res                  # ni la lista de parecidos

    # Verificar como usuario: aceptado, sin foto de la BD
    _, r, _ = peticion(servidor, "POST", "/api/verificar?id=Eileen%20Collins", foto)
    assert r["resultado"]["aceptado"] is True and "foto" not in r["resultado"]


# --- Inscripción abierta temporal (el "+") ---------------------------------------------------

def _servidor_abierto(config_web, minutos):
    srv = crear_servidor(config_web, "127.0.0.1", 0, admin_pass=PIN_ADMIN, inscripcion_abierta_min=minutos)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_el_mas_no_existe_por_defecto(servidor, ruta_astronauta):
    """Sin la opción de arranque, un usuario sigue sin poder inscribir."""
    _, estado, _ = peticion(servidor, "GET", "/api/estado")
    assert estado["inscripcion_abierta"] is False and estado["inscripcion_segundos"] == 0
    estado_http, r, _ = peticion(servidor, "POST", "/api/inscribir?id=Amigo", ruta_astronauta.read_bytes())
    assert estado_http == 403 and r["error"]["error"] == "requiere_admin"


def test_inscripcion_abierta_solo_permite_personas_nuevas(config_web, ruta_astronauta):
    srv = _servidor_abierto(config_web, 5)
    try:
        puerto = srv.server_address[1]
        foto = ruta_astronauta.read_bytes()
        _, estado, _ = peticion(puerto, "GET", "/api/estado")
        assert estado["inscripcion_abierta"] is True and 0 < estado["inscripcion_segundos"] <= 300

        # Un usuario (sin PIN) añade una persona NUEVA
        e, r, _ = peticion(puerto, "POST", "/api/inscribir?id=Amigo%20Uno", foto)
        assert e == 200 and r["ok"] and r["resultado"]["nueva_persona"]
        assert "foto" not in r["resultado"]  # sigue sin ver fotos de la BD

        # ...pero NO puede añadir muestras a alguien que existe y no es del modo abierto
        cab = {"Cookie": cookie_admin(puerto)}
        peticion(puerto, "POST", "/api/inscribir?id=Persona%20Real", foto, cab)  # la inscribe el admin
        e, r, _ = peticion(puerto, "POST", "/api/inscribir?id=Persona%20Real", foto)
        assert e == 403 and r["error"]["error"] == "nombre_ocupado"
        # Las demás acciones de admin siguen cerradas
        assert peticion(puerto, "GET", "/api/personas")[0] == 403
        assert peticion(puerto, "DELETE", "/api/personas?id=Amigo%20Uno")[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_inscripcion_abierta_tiene_tope(config_web, ruta_astronauta):
    srv = _servidor_abierto(config_web, 5)
    try:
        puerto = srv.server_address[1]
        app = srv.RequestHandlerClass.app
        app._inscritos_abiertos.update(f"Relleno {i}" for i in range(app.MAX_INSCRITOS_ABIERTOS))
        e, r, _ = peticion(puerto, "POST", "/api/inscribir?id=Uno%20Mas", ruta_astronauta.read_bytes())
        assert e == 403 and r["error"]["error"] == "inscripcion_llena"
    finally:
        srv.shutdown()
        srv.server_close()


def test_inscripcion_abierta_caduca(config_web, ruta_astronauta):
    srv = _servidor_abierto(config_web, 5)
    try:
        puerto = srv.server_address[1]
        app = srv.RequestHandlerClass.app
        app._abierta_hasta = 1.0  # ya pasó (time.monotonic() es mucho mayor)
        assert peticion(puerto, "GET", "/api/estado")[1]["inscripcion_abierta"] is False
        e, r, _ = peticion(puerto, "POST", "/api/inscribir?id=Tarde", ruta_astronauta.read_bytes())
        assert e == 403 and r["error"]["error"] == "requiere_admin"
    finally:
        srv.shutdown()
        srv.server_close()
