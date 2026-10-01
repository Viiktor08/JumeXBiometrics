"""Tests del extractor SFace y de la demo web JumeX Lens (API y galería)."""

import http.client
import json
import threading

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE
from biometria.config import RAIZ_REPO
from biometria.errores import ConfiguracionInvalida, IdInvalido, TemplateInvalido
from biometria.extractores import crear_extractor
from biometria.extractores.sface import MODELO_POR_DEFECTO, ExtractorSFace
from biometria.web.analisis import Galeria, analizar
from biometria.web.servidor import crear_servidor
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
def servidor(tmp_path, monkeypatch, detector):
    """Servidor real en un puerto libre, con config LBP y BD temporal."""
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    texto = (RAIZ_REPO / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("ruta: data/bd.csv", f"ruta: {tmp_path / 'bd.csv'}")
    texto = texto.replace("ruta_clave: data/clave_hmac.key", f"ruta_clave: {tmp_path / 'k.key'}")
    texto = texto.replace("modelo: models/", f"modelo: {RAIZ_REPO / 'models'}/")
    config = tmp_path / "config.yaml"
    config.write_text(texto, encoding="utf-8")
    srv = crear_servidor(config, "127.0.0.1", 0)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


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
    assert "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    assert peticion(servidor, "GET", "/static/app.js")[0] == 200
    assert peticion(servidor, "GET", "/static/../bd.py")[0] == 404
    assert peticion(servidor, "GET", "/static/no_existe.js")[0] == 404


def test_web_flujo_inscribir_verificar_identificar(servidor, ruta_astronauta):
    foto = ruta_astronauta.read_bytes()
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=Eileen%20Collins", foto)
    assert estado == 200 and r["ok"] and r["resultado"]["nueva_persona"]
    assert r["detecciones"][0]["elegida"] and r["cara_alineada"].startswith("data:image/png;base64,")
    assert r["template"]["dimension"] == 2891

    _, personas, _ = peticion(servidor, "GET", "/api/personas")
    [p] = personas["personas"]
    assert p["id"] == "Eileen Collins" and p["foto"].startswith("/api/caras/")
    estado, imagen, _ = peticion(servidor, "GET", p["foto"])
    assert estado == 200 and imagen[:2] == b"\xff\xd8"  # JPEG

    _, r, _ = peticion(servidor, "POST", "/api/verificar?id=Eileen%20Collins", foto)
    assert r["resultado"]["aceptado"] is True and r["resultado"]["foto"] == p["foto"]

    _, r, _ = peticion(servidor, "POST", "/api/identificar", foto)
    assert r["resultado"]["id"] == "Eileen Collins"
    assert r["resultado"]["candidatos"][0]["id"] == "Eileen Collins"

    _, estado_sistema, _ = peticion(servidor, "GET", "/api/estado")
    assert estado_sistema["personas"] == 1 and estado_sistema["integridad"] == "ok"


def test_web_errores_controlados(servidor, ruta_astronauta):
    foto = ruta_astronauta.read_bytes()
    _, r, _ = peticion(servidor, "POST", "/api/verificar?id=Nadie", foto)
    assert r["ok"] is False and r["error"]["error"] == "id_no_inscrito"
    assert len(r["detecciones"]) == 1  # se enseña la detección aunque falle después
    _, r, _ = peticion(servidor, "POST", "/api/identificar", b"basura")
    assert r["error"]["error"] == "imagen_invalida"
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=", foto)
    assert estado == 400
    estado, r, _ = peticion(servidor, "POST", "/api/inscribir?id=%3D1%2B1", foto)
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
    assert peticion(servidor, "GET", "/api/caras/../../k.key")[0] == 404


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


def test_web_analizar_no_toca_la_bd(servidor, ruta_astronauta):
    peticion(servidor, "POST", "/api/analizar", ruta_astronauta.read_bytes())
    _, estado, _ = peticion(servidor, "GET", "/api/estado")
    assert estado["personas"] == 0


def test_web_eliminar_persona(servidor, ruta_astronauta):
    foto = ruta_astronauta.read_bytes()
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto)
    _, personas, _ = peticion(servidor, "GET", "/api/personas")
    url_foto = personas["personas"][0]["foto"]
    estado, r, _ = peticion(servidor, "DELETE", "/api/personas?id=Eileen")
    assert estado == 200 and r["resultado"]["muestras_borradas"] == 1
    assert peticion(servidor, "GET", "/api/personas")[1]["personas"] == []
    assert peticion(servidor, "GET", url_foto)[0] == 404  # el retrato también se borra
    assert peticion(servidor, "DELETE", "/api/personas?id=Eileen")[0] == 404
    conexion = http.client.HTTPConnection("127.0.0.1", servidor, timeout=30)
    conexion.request("DELETE", "/api/personas?id=Eileen")  # sin cabecera
    assert conexion.getresponse().status == 403
    conexion.close()



def test_web_identificar_varias_personas(servidor, astronauta):
    # Dos copias de la misma persona y una tercera cara que no está inscrita (girada en espejo
    # no vale: sería la misma persona). Basta con comprobar que cada cara recibe su resultado.
    ok, foto_una = cv2.imencode(".png", astronauta)
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto_una.tobytes())
    ok, foto_dos = cv2.imencode(".png", np.hstack([astronauta, astronauta]))
    _, r, _ = peticion(servidor, "POST", "/api/identificar", foto_dos.tobytes())
    assert r["ok"] and r["multiple"] and len(r["caras"]) == 2
    assert [c["numero"] for c in r["caras"]] == [1, 2]
    assert r["caras"][0]["caja"][0] < r["caras"][1]["caja"][0]          # izquierda a derecha
    assert all(c["estado"] == "identificada" and c["id"] == "Eileen" for c in r["caras"])
    assert all(c["retrato"].startswith("data:image/jpeg") for c in r["caras"])
    assert r["resultado"]["identificadas"] == 2 and r["resultado"]["desconocidas"] == 0
    # Marcando una cara a mano se vuelve al modo de una sola cara
    x, y, w, h = r["caras"][1]["caja"]
    _, r, _ = peticion(servidor, "POST", f"/api/identificar?caja={x},{y},{w},{h}", foto_dos.tobytes())
    assert not r.get("multiple") and r["resultado"]["id"] == "Eileen"


def test_web_ficha_y_edicion_de_persona(servidor, ruta_astronauta, astronauta):
    foto = ruta_astronauta.read_bytes()
    peticion(servidor, "POST", "/api/inscribir?id=Eileen", foto)
    ok, girada = cv2.imencode(".png", cv2.flip(astronauta, 1))
    _, r, _ = peticion(servidor, "POST", "/api/inscribir?id=Eileen", girada.tobytes())
    assert r["resultado"]["muestras"] == 2 and len(r["resultado"]["clave"]) == 16

    _, ficha, _ = peticion(servidor, "GET", "/api/persona?id=Eileen")
    assert [m["principal"] for m in ficha["muestras"]] == [True, False]
    primera, segunda = (m["clave"] for m in ficha["muestras"])
    assert all(peticion(servidor, "GET", m["foto"])[0] == 200 for m in ficha["muestras"])

    # Elegir la segunda como principal
    _, ficha, _ = peticion(servidor, "POST", f"/api/persona/principal?id=Eileen&clave={segunda}")
    assert ficha["foto"].endswith(f"{segunda}.jpg")
    assert peticion(servidor, "POST", "/api/persona/principal?id=Eileen&clave=" + "0" * 16)[0] == 404

    # Borrar una muestra
    _, r, _ = peticion(servidor, "DELETE", f"/api/muestras?id=Eileen&clave={primera}")
    assert r["resultado"]["quedan"] == 1
    assert peticion(servidor, "DELETE", f"/api/muestras?id=Eileen&clave={primera}")[0] == 404

    # Renombrar: las fotos se mueven con la persona
    _, ficha, _ = peticion(servidor, "POST", "/api/persona/renombrar?id=Eileen&nuevo=Eileen%20Collins")
    assert ficha["id"] == "Eileen Collins" and peticion(servidor, "GET", ficha["foto"])[0] == 200
    assert peticion(servidor, "GET", "/api/persona?id=Eileen")[0] == 404
    assert peticion(servidor, "POST", "/api/persona/renombrar?id=Eileen%20Collins&nuevo=%3Dmal")[0] == 400

    # Borrar la última muestra elimina a la persona
    _, r, _ = peticion(servidor, "DELETE", f"/api/muestras?id=Eileen%20Collins&clave={segunda}")
    assert r["resultado"]["quedan"] == 0
    assert peticion(servidor, "GET", "/api/personas")[1]["personas"] == []


def test_web_dominio_del_tunel(tmp_path, monkeypatch, detector):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    texto = (RAIZ_REPO / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("ruta: data/bd.csv", f"ruta: {tmp_path / 'bd.csv'}")
    texto = texto.replace("ruta_clave: data/clave_hmac.key", f"ruta_clave: {tmp_path / 'k.key'}")
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
