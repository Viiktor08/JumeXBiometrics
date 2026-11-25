"""Tests de la interfaz de extractores y del extractor LBP."""

import numpy as np
import pytest

from biometria.errores import ConfiguracionInvalida, TemplateInvalido
from biometria.extractores import EXTRACTORES, ExtractorLBP, crear_extractor
from biometria.extractores.lbp import tabla_uniforme
from tests.conftest import cfg_con

import cv2  # noqa: I001


@pytest.fixture(scope="module")
def lbp():
    return ExtractorLBP(radio=1, puntos=8, rejilla=(7, 7))


@pytest.fixture(scope="module")
def cara(detector, astronauta):
    return detector.procesar(astronauta).cara


# --- Registro y configuración -------------------------------------------------

def test_crear_extractor_desde_config(cfg):
    extractor = crear_extractor(cfg)
    assert isinstance(extractor, EXTRACTORES[cfg.extractor.nombre])


def test_extractor_desconocido(cfg):
    with pytest.raises(ConfiguracionInvalida, match="desconocido"):
        crear_extractor(cfg_con(cfg, extractor={"nombre": "no_existe"}))


@pytest.mark.parametrize("parametros", [
    {"radio": 0},
    {"puntos": 32},
    {"rejilla": [7]},
    {"rejilla": [0, 7]},
    {"parametro_raro": 1},
])
def test_parametros_invalidos(cfg, parametros):
    with pytest.raises(ConfiguracionInvalida, match="Parámetros no válidos"):
        crear_extractor(cfg_con(cfg, extractor={"nombre": "lbp", "parametros": parametros}))


def test_rejilla_demasiado_fina_para_tam_cara(cfg):
    pequeña = cfg_con(cfg, preprocesado={"tam_cara": (20, 20)},
                      extractor={"nombre": "lbp", "parametros": {"radio": 3, "rejilla": [16, 16]}})
    with pytest.raises(ConfiguracionInvalida, match="demasiado pequeña"):
        crear_extractor(pequeña)


# --- LBP: códigos y patrones uniformes ------------------------------------------

@pytest.mark.parametrize("puntos", [4, 8, 12, 16])
def test_tabla_uniforme_tiene_p_por_p_menos_1_mas_3_clases(puntos):
    tabla = tabla_uniforme(puntos)
    assert len(np.unique(tabla)) == puntos * (puntos - 1) + 3
    assert tabla[0] != tabla[(1 << puntos) - 1]       # todo 0 y todo 1 son uniformes distintos
    assert tabla[0b0101] == puntos * (puntos - 1) + 2  # 4 transiciones -> no uniforme


def test_codigo_lbp_a_mano(lbp):
    # Vecinos en orden: derecha, arriba-dcha, arriba, arriba-izq, izq, abajo-izq, abajo, abajo-dcha
    # (bit 0 = derecha, sentido antihorario). Centro 50: vecino >= 50 -> 1.
    parche = np.array([
        [60, 10, 10],
        [90, 50, 50],   # derecha == centro -> 1 (empate cuenta como 1)
        [10, 10, 70],
    ], dtype=np.uint8)
    esperado = (1 << 0) | (1 << 3) | (1 << 4) | (1 << 7)
    assert lbp.codigos(parche)[0, 0] == esperado


def test_imagen_plana_da_un_solo_patron(lbp):
    plana = np.full((112, 112), 128, dtype=np.uint8)
    codigos = lbp.codigos(plana)
    assert np.all(codigos == 255)  # todos los vecinos == centro -> todos 1


# --- LBP: templates ----------------------------------------------------------------

def test_template_dimension_tipo_y_normalizacion(lbp, cara):
    t = lbp.extraer(cara)
    assert t.shape == (lbp.dimension,) == (59 * 49,)
    assert t.dtype == np.float32
    assert np.all(t >= 0)
    sumas = t.reshape(49, 59).sum(axis=1)
    assert np.allclose(sumas, 1 / 49, atol=1e-6)


def test_extraer_es_determinista_y_acepta_gris(lbp, cara):
    gris = cv2.cvtColor(cara, cv2.COLOR_BGR2GRAY)
    assert np.array_equal(lbp.extraer(cara), lbp.extraer(cara))
    assert np.array_equal(lbp.extraer(cara), lbp.extraer(gris))


def test_invariante_a_cambios_monotonos_de_brillo(lbp, cara):
    # LBP solo mira el orden de los grises: un cambio monótono no lo altera.
    gris = cv2.cvtColor(cara, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gamma = (255 * (gris / 255) ** 0.5).round().astype(np.uint8)
    mas_oscura = (gris * 0.5).round().astype(np.uint8)
    t = lbp.extraer(cara)
    # El redondeo a uint8 puede crear algún empate nuevo: casi idénticos, no exactos.
    assert lbp.comparar(t, lbp.extraer(gamma)) > 0.95
    assert lbp.comparar(t, lbp.extraer(mas_oscura)) > 0.95


def test_otras_configuraciones(cara):
    for extractor in (ExtractorLBP(radio=2, puntos=16, rejilla=(8, 8)),
                      ExtractorLBP(radio=1.5, puntos=12, rejilla=(4, 6))):
        t = extractor.extraer(cara)
        assert t.shape == (extractor.dimension,)
        assert extractor.validar_template(t) is not None


# --- LBP: comparación -----------------------------------------------------------------

def test_comparar_consigo_mismo_es_1(lbp, cara):
    t = lbp.extraer(cara)
    assert lbp.comparar(t, t) == pytest.approx(1.0)


def test_comparar_es_simetrica_y_en_0_1(lbp, cara):
    a = lbp.extraer(cara)
    b = lbp.extraer(cv2.GaussianBlur(cara, (5, 5), 0))
    assert 0.0 <= lbp.comparar(a, b) <= 1.0
    assert lbp.comparar(a, b) == pytest.approx(lbp.comparar(b, a))


def test_templates_sin_nada_en_comun_dan_0(lbp):
    a = np.zeros(lbp.dimension, dtype=np.float32)
    b = np.zeros(lbp.dimension, dtype=np.float32)
    a[0::lbp.n_clases] = 1 / lbp.n_celdas  # cada celda: todo en la clase 0
    b[1::lbp.n_clases] = 1 / lbp.n_celdas  # cada celda: todo en la clase 1
    assert lbp.comparar(a, b) == pytest.approx(0.0, abs=1e-6)  # redondeo de float32


def test_misma_cara_puntua_mas_que_otra_imagen(lbp, detector, astronauta, cara):
    # La misma persona ligeramente girada se parece más que una textura cualquiera.
    alto, ancho = astronauta.shape[:2]
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), 8, 1.0)
    girada = cv2.warpAffine(astronauta, m, (ancho, alto), borderMode=cv2.BORDER_REFLECT)
    ruido = np.random.default_rng(0).integers(0, 256, cara.shape, dtype=np.uint8)
    t = lbp.extraer(cara)
    misma = lbp.comparar(t, lbp.extraer(detector.procesar(girada).cara))
    otra = lbp.comparar(t, lbp.extraer(ruido))
    assert misma > otra


# --- Validación de templates (entrada de los métodos _t) ------------------------------

def test_validar_acepta_listas(lbp, cara):
    t = lbp.extraer(cara)
    assert np.array_equal(lbp.validar_template(t.tolist()), t)


@pytest.mark.parametrize("caso", ["corto", "2d", "nan", "inf", "negativo", "sin_normalizar", "texto"])
def test_validar_rechaza_templates_invalidos(lbp, cara, caso):
    t = lbp.extraer(cara).copy()
    if caso == "corto":
        t = t[:-1]
    elif caso == "2d":
        t = t.reshape(49, 59)
    elif caso == "nan":
        t[0] = np.nan
    elif caso == "inf":
        t[0] = np.inf
    elif caso == "negativo":
        t[0], t[1] = -0.001, t[1] + 0.001  # sigue sumando lo mismo
    elif caso == "sin_normalizar":
        t = t * 2
    elif caso == "texto":
        t = ["a"] * lbp.dimension
    with pytest.raises(TemplateInvalido):
        lbp.validar_template(t)


def test_comparar_valida_los_templates(lbp, cara):
    t = lbp.extraer(cara)
    with pytest.raises(TemplateInvalido):
        lbp.comparar(t, np.ones(lbp.dimension))
