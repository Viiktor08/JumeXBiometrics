"""Tests de las contramedidas frente a ataques (ver THREAT_MODEL.md).

Cada test monta una BD temporal, lanza el ataque como lo haría el grupo rival
(solo métodos públicos) y comprueba que la defensa lo para sin estorbar al uso
legítimo. Necesitan los modelos y, la primera vez, la foto de prueba.
"""

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE
from biometria.config import RAIZ_REPO
from biometria.errores import DemasiadosIntentos, MuestraIncoherente
from biometria.extractores.arcface import MODELO_POR_DEFECTO
from biometria.sistema import SistemaBiometrico
from tests.conftest import cfg_con

import cv2  # noqa: I001

pytestmark = pytest.mark.skipif(not (RAIZ_REPO / MODELO_POR_DEFECTO).is_file(),
                                reason="Falta el modelo ArcFace: python scripts/descargar_modelos.py")


@pytest.fixture
def cfg_tmp(cfg, tmp_path, monkeypatch, detector):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    return cfg_con(cfg,
                   bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "k.key"},
                   defensa={"coherencia_muestra": 0.20})


# --- Paso 1: envenenar la inscripción ---------------------------------------

def test_coherencia_bloquea_envenenamiento(cfg_tmp, ruta_astronauta, detector, astronauta):
    """Añadir una cara ajena como muestra de una persona existente se rechaza,
    pero otra foto de la misma persona se acepta."""
    sistema = SistemaBiometrico(cfg_tmp)
    sistema.inscribir("Eileen Collins", ruta_astronauta)

    # Otra foto de la MISMA persona (la astronauta con un cambio de brillo leve): se acepta
    alto, ancho = astronauta.shape[:2]
    variacion = cv2.convertScaleAbs(astronauta, alpha=1.08, beta=8)
    r = sistema.inscribir("Eileen Collins", variacion)
    assert r.muestras == 2

    # Una cara AJENA (ruido con estructura, que ArcFace ve lejísimos): se rechaza
    ajena = sistema.extractor.extraer(
        np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    with pytest.raises(MuestraIncoherente):
        sistema.inscribir_t("Eileen Collins", ajena)


def test_coherencia_no_estorba_a_una_persona_nueva(cfg_tmp):
    """La primera muestra de alguien nunca se rechaza: no hay con qué comparar."""
    sistema = SistemaBiometrico(cfg_tmp)
    ajena = sistema.extractor.extraer(
        np.random.default_rng(2).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    assert sistema.inscribir_t("Quien Sea", ajena).muestras == 1


def test_coherencia_desactivada_deja_pasar_el_ataque(cfg, tmp_path, monkeypatch, detector, ruta_astronauta):
    """Con coherencia_muestra: 0 el envenenamiento vuelve a funcionar (prueba de
    que la defensa es la que lo para, no otra cosa)."""
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    cfg_sin = cfg_con(cfg, bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "k.key"},
                      defensa={"coherencia_muestra": 0.0})
    sistema = SistemaBiometrico(cfg_sin)
    sistema.inscribir("Eileen Collins", ruta_astronauta)
    ajena = sistema.extractor.extraer(
        np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    assert sistema.inscribir_t("Eileen Collins", ajena).muestras == 2  # entra sin problema


# --- Paso 2: firma de templates (contra templates fabricados en _t) ----------

def test_firma_distingue_template_propio_de_fabricado(cfg_tmp, ruta_astronauta):
    sistema = SistemaBiometrico(cfg_tmp)
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    firma = sistema.firmar(t)

    # Firmado por nosotros: fiable=True
    assert sistema.verificar_t("Eileen", t, firma).fiable is True
    assert sistema.identificar_t(t, firma).fiable is True
    # Sin firma: fiable=None (no se evaluó)
    assert sistema.verificar_t("Eileen", t).fiable is None
    # Firma incorrecta (template fabricado o manipulado): fiable=False
    assert sistema.verificar_t("Eileen", t, "ab" * 32).fiable is False


def test_la_firma_depende_de_la_clave(cfg, tmp_path, monkeypatch, detector, ruta_astronauta):
    """Un template firmado por OTRO sistema (otra clave) no se acepta como fiable."""
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    a = SistemaBiometrico(cfg_con(cfg, bd={"ruta": tmp_path / "a.csv", "ruta_clave": tmp_path / "a.key"}))
    b = SistemaBiometrico(cfg_con(cfg, bd={"ruta": tmp_path / "b.csv", "ruta_clave": tmp_path / "b.key"}))
    a.inscribir("Eileen", ruta_astronauta)
    b.inscribir("Eileen", ruta_astronauta)
    t = a.extraer(ruta_astronauta)
    firma_de_a = a.firmar(t)
    assert a.verificar_t("Eileen", t, firma_de_a).fiable is True
    assert b.verificar_t("Eileen", t, firma_de_a).fiable is False  # la clave de B es otra


# --- Paso 3: anti hill-climbing (redondeo + límite de intentos) --------------

def test_la_puntuacion_de_t_se_redondea(cfg_tmp, ruta_astronauta):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"decimales_puntuacion_t": 2}))
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    p = sistema.verificar_t("Eileen", t).puntuacion
    assert p == round(p, 2)  # no hay más de 2 decimales de información


def test_limite_de_intentos_frena_el_barrido(cfg_tmp, ruta_astronauta):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"max_intentos_t": 5, "ventana_intentos_s": 60}))
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    for _ in range(5):
        sistema.verificar_t("Eileen", t)  # los 5 primeros pasan
    with pytest.raises(DemasiadosIntentos):
        sistema.verificar_t("Eileen", t)  # el sexto, en la misma ventana, se frena
    # El límite es POR id: otra persona tiene su propio cupo
    sistema.inscribir("Otra", ruta_astronauta)
    assert sistema.verificar_t("Otra", t).id == "Otra"


def test_las_operaciones_con_foto_no_gastan_el_cupo(cfg_tmp, ruta_astronauta):
    """verificar/identificar (con foto) no cuentan para el límite: ahí se ve la cara,
    no hay hill-climbing por template."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"max_intentos_t": 2}))
    sistema.inscribir("Eileen", ruta_astronauta)
    for _ in range(10):
        sistema.verificar("Eileen", ruta_astronauta)  # nunca se frena
    assert sistema.identificar(ruta_astronauta).id == "Eileen"
