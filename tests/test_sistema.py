"""Tests de SistemaBiometrico (los 9 métodos) y de la CLI."""

import re
import json
from pathlib import Path

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE
from biometria.cli import cargar_template, guardar_template, main
from biometria.errores import (
    IdInvalido,
    IdNoInscrito,
    ImagenInvalida,
    TemplateInvalido,
    VariasCaras,
)
from biometria.sistema import SistemaBiometrico
from tests.conftest import cfg_con

import cv2  # noqa: I001


@pytest.fixture
def cfg_tmp(cfg, tmp_path, monkeypatch, detector):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    # Comportamiento base de los 9 métodos: sin las defensas que lo cambian a
    # propósito (cada una tiene sus tests en test_defensas.py)
    return cfg_con(cfg, bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "clave.key"},
                   defensa={"exigir_firma_t": False, "cara_en_sd": False, "margen_identificacion": 0.0})


@pytest.fixture
def sistema(cfg_tmp):
    return SistemaBiometrico(cfg_tmp)


@pytest.fixture
def ruido():
    """Imagen sin cara, con textura (pasa el control de nitidez en los _sd)."""
    return np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8)


@pytest.fixture
def cara_astronauta(detector, astronauta):
    return detector.procesar(astronauta).cara


# --- Pipeline completo ------------------------------------------------------------

def test_inscribir_verificar_identificar(sistema, ruta_astronauta):
    r = sistema.inscribir("Eileen Collins", ruta_astronauta)
    assert (r.id, r.muestras) == ("Eileen Collins", 1)

    v = sistema.verificar("Eileen Collins", ruta_astronauta)
    assert v.aceptado and v.puntuacion == pytest.approx(1.0)

    i = sistema.identificar(ruta_astronauta)
    assert i.identificado and i.id == "Eileen Collins"


def test_acepta_ruta_bytes_y_array(sistema, ruta_astronauta, astronauta):
    sistema.inscribir("Eileen", ruta_astronauta)
    for foto in (str(ruta_astronauta), ruta_astronauta.read_bytes(), astronauta):
        assert sistema.verificar("Eileen", foto).aceptado


def test_verificar_id_no_inscrito_antes_de_procesar_la_foto(sistema, ruta_astronauta):
    sistema.inscribir("Eileen", ruta_astronauta)
    # La foto ni siquiera existe: falla por el id, sin llegar a leerla.
    with pytest.raises(IdNoInscrito):
        sistema.verificar("Otra", "no_existe.jpg")


def test_id_invalido_antes_de_procesar_la_foto(sistema):
    with pytest.raises(IdInvalido):
        sistema.inscribir("=1+1", "no_existe.jpg")


def test_errores_del_detector_llegan_al_llamador(sistema, astronauta):
    doble = np.hstack([astronauta, astronauta])
    with pytest.raises(VariasCaras):
        sistema.inscribir("Eileen", doble)
    with pytest.raises(ImagenInvalida):
        sistema.inscribir("Eileen", b"esto no es una imagen")
    assert not sistema.bd.ruta.exists()  # nada se ha inscrito


def test_identificar_con_bd_vacia(sistema, ruta_astronauta):
    i = sistema.identificar(ruta_astronauta)
    assert i.id is None and i.puntuacion is None and not i.identificado


def test_identificar_por_debajo_del_umbral_es_desconocido(cfg_tmp, ruta_astronauta, ruido):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, umbrales={"identificacion": 0.99}))
    sistema.inscribir_sd("Ruido", ruido)
    i = sistema.identificar(ruta_astronauta)
    assert i.id is None
    assert 0.0 <= i.puntuacion < 0.99  # se informa de la mejor puntuación aunque no llegue


def test_identificar_elige_al_mas_parecido(sistema, ruta_astronauta, ruido):
    sistema.inscribir_sd("Ruido", ruido)
    sistema.inscribir("Eileen", ruta_astronauta)
    assert sistema.identificar(ruta_astronauta).id == "Eileen"


def test_umbral_de_verificacion(cfg_tmp, ruta_astronauta, ruido):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, umbrales={"verificacion": 0.99}))
    sistema.inscribir_sd("Ruido", ruido)
    v = sistema.verificar("Ruido", ruta_astronauta)
    assert not v.aceptado and v.umbral == 0.99


@pytest.mark.parametrize("agregacion", ["max", "media"])
def test_agregacion_de_muestras(cfg_tmp, cara_astronauta, ruido, agregacion):
    # Mezclar a propósito cara + ruido bajo un id es lo que la coherencia bloquea;
    # aquí se prueba la AGREGACIÓN, así que se desactiva esa defensa.
    # Sin redondeo de la puntuación (defensa anti hill-climbing) para comparar el valor exacto
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, bd={"agregacion": agregacion},
                                        defensa={"coherencia_muestra": 0.0, "decimales_puntuacion_t": 0}))
    sistema.inscribir_sd("P", cara_astronauta)
    sistema.inscribir_sd("P", ruido)
    t = sistema.extraer_sd(cara_astronauta)
    puntuaciones = [sistema.extractor.comparar(t, m) for m in sistema.bd.templates_de("P")]
    esperado = max(puntuaciones) if agregacion == "max" else np.mean(puntuaciones)
    assert sistema.verificar_t("P", t).puntuacion == pytest.approx(esperado)


# --- Sin detector (_sd) -------------------------------------------------------------

def test_metodos_sd(sistema, cara_astronauta):
    assert sistema.inscribir_sd("Eileen", cara_astronauta).muestras == 1
    assert sistema.verificar_sd("Eileen", cara_astronauta).puntuacion == pytest.approx(1.0)
    assert sistema.identificar_sd(cara_astronauta).id == "Eileen"


def test_sd_no_usa_el_detector(sistema, ruido):
    # Una imagen sin cara se acepta en _sd (superficie de ataque conocida y documentada)
    assert sistema.inscribir_sd("Ruido", ruido).muestras == 1


# --- Con template (_t) ----------------------------------------------------------------

def test_metodos_t(sistema, ruta_astronauta):
    t = sistema.extraer(ruta_astronauta)
    assert sistema.inscribir_t("Eileen", t).muestras == 1
    assert sistema.verificar_t("Eileen", t).aceptado
    assert sistema.identificar_t(t.tolist()).id == "Eileen"  # también acepta listas


def test_t_coincide_con_el_pipeline_completo(sistema, ruta_astronauta):
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    por_t = sistema.verificar_t("Eileen", t)
    por_foto = sistema.verificar("Eileen", ruta_astronauta)
    # Misma decisión e id; la puntuación de _t va redondeada (defensa anti hill-climbing)
    assert por_t.id == por_foto.id and por_t.aceptado == por_foto.aceptado
    assert por_t.puntuacion == pytest.approx(por_foto.puntuacion, abs=0.01)


@pytest.mark.parametrize("template", [np.ones(10), [float("nan")] * 512, "texto"])
def test_t_rechaza_templates_invalidos(sistema, ruta_astronauta, template):
    sistema.inscribir("Eileen", ruta_astronauta)
    with pytest.raises(TemplateInvalido):
        sistema.verificar_t("Eileen", template)
    with pytest.raises(TemplateInvalido):
        sistema.identificar_t(template)
    with pytest.raises(TemplateInvalido):
        sistema.inscribir_t("Otra", template)


def test_t_no_necesita_el_modelo_del_detector(cfg_tmp, tmp_path, cara_astronauta):
    sin_modelo = cfg_con(cfg_tmp, detector={"modelo": tmp_path / "no_existe.onnx"})
    sistema = SistemaBiometrico(sin_modelo)
    t = sistema.extractor.extraer(cara_astronauta)
    sistema.inscribir_t("Eileen", t)
    assert sistema.identificar_t(t).id == "Eileen"


# --- CLI ---------------------------------------------------------------------------------

@pytest.fixture
def config_yaml(cfg_tmp, tmp_path):
    """config.yaml temporal con la BD en tmp_path."""
    ruta = tmp_path / "config.yaml"
    raiz = Path(__file__).resolve().parent.parent
    texto = (raiz / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("ruta: data/bd.csv", f"ruta: {cfg_tmp.bd.ruta}")
    re_clave = f"ruta_clave: {cfg_tmp.bd.ruta_clave}"
    texto = re.sub(r"ruta_clave: \S+", lambda _: re_clave, texto)
    texto = texto.replace("modelo: models/", f"modelo: {raiz / 'models'}/")
    ruta.write_text(texto, encoding="utf-8")
    return str(ruta)


def ejecutar(capsys, *argumentos):
    codigo = main(list(argumentos))
    return codigo, json.loads(capsys.readouterr().out)


def test_cli_flujo_completo(capsys, config_yaml, ruta_astronauta, tmp_path):
    c = ["--config", config_yaml]
    foto = str(ruta_astronauta)
    assert ejecutar(capsys, *c, "inscribir", "--id", "Eileen", "--foto", foto) == (
        0, {"operacion": "inscribir", "id": "Eileen", "muestras": 1})

    codigo, salida = ejecutar(capsys, *c, "verificar", "--id", "Eileen", "--foto", foto)
    assert codigo == 0 and salida["aceptado"] is True

    codigo, salida = ejecutar(capsys, *c, "identificar", "--foto", foto)
    assert codigo == 0 and salida["id"] == "Eileen" and salida["identificado"] is True

    plantilla = str(tmp_path / "t.npy")
    codigo, salida = ejecutar(capsys, *c, "extraer", "--foto", foto, "--salida", plantilla)
    assert codigo == 0 and salida["dimension"] == 512  # ArcFace
    codigo, salida = ejecutar(capsys, *c, "verificar_t", "--id", "Eileen", "--template", plantilla)
    assert salida["operacion"] == "verificar_t" and salida["aceptado"] is True

    codigo, salida = ejecutar(capsys, *c, "bd")
    assert salida["integridad"] == "ok" and salida["ids"] == {"Eileen": 1}


def test_cli_errores_en_json_con_codigo_1(capsys, config_yaml, ruta_astronauta):
    codigo, salida = ejecutar(capsys, "--config", config_yaml, "verificar",
                              "--id", "Nadie", "--foto", str(ruta_astronauta))
    assert codigo == 1
    assert salida == {"error": "id_no_inscrito", "mensaje": "'Nadie' no está inscrito"}


def test_cli_verificacion_rechazada_sale_con_0(capsys, config_yaml, ruta_astronauta, tmp_path, ruido):
    ruta_ruido = tmp_path / "ruido.png"
    cv2.imwrite(str(ruta_ruido), ruido)
    c = ["--config", config_yaml]
    # Sin la comprobación de cara en _sd (paso 5): aquí interesa una muestra lejana cualquiera
    Path(config_yaml).write_text(Path(config_yaml).read_text().replace("cara_en_sd: true", "cara_en_sd: false"))
    ejecutar(capsys, *c, "inscribir_sd", "--id", "Ruido", "--foto", str(ruta_ruido))
    texto = re.sub(r"verificacion: [0-9.]+", "verificacion: 0.99", Path(config_yaml).read_text())
    Path(config_yaml).write_text(texto)
    codigo, salida = ejecutar(capsys, *c, "verificar", "--id", "Ruido", "--foto", str(ruta_astronauta))
    assert codigo == 0 and salida["aceptado"] is False


def test_cli_estado_sin_comando(capsys):
    codigo, salida = ejecutar(capsys)
    assert codigo == 0 and "version" in salida


def test_cli_argumentos_incorrectos():
    with pytest.raises(SystemExit) as e:
        main(["verificar", "--id", "Ana"])  # falta --foto
    assert e.value.code == 2


# --- Ficheros de template ------------------------------------------------------------------

@pytest.mark.parametrize("extension", [".npy", ".json", ".txt"])
def test_template_ida_y_vuelta(tmp_path, extension):
    t = np.random.default_rng(0).random(2891).astype(np.float32)
    ruta = tmp_path / f"t{extension}"
    guardar_template(t, ruta)
    assert np.array_equal(cargar_template(ruta).astype(np.float32), t)


def test_template_npy_con_pickle_se_rechaza(tmp_path):
    ruta = tmp_path / "malicioso.npy"
    np.save(ruta, np.array([{"payload": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(TemplateInvalido):
        cargar_template(ruta)


def test_template_fichero_enorme_se_rechaza(tmp_path):
    ruta = tmp_path / "grande.json"
    ruta.write_text("[" + "0," * 600_000 + "0]")
    with pytest.raises(TemplateInvalido, match="supera"):
        cargar_template(ruta)


@pytest.mark.parametrize("contenido", ["no es base64 !!", "[1, 2, \"x\"]", "{\"a\": 1}"])
def test_template_mal_formado(tmp_path, contenido):
    ruta = tmp_path / ("t.json" if contenido.startswith(("[", "{")) else "t.txt")
    ruta.write_text(contenido)
    with pytest.raises(TemplateInvalido):
        sistema_extractor_valida(cargar_template(ruta))


def sistema_extractor_valida(template):
    """Un template leído del disco siempre pasa después por validar_template."""
    from biometria.extractores import ExtractorLBP
    return ExtractorLBP().validar_template(template)
