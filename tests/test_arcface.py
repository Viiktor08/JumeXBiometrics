"""Tests del extractor ArcFace (el asignado) y de la migración desde la galería."""

import sys

import numpy as np
import pytest

from biometria.config import RAIZ_REPO
from biometria.errores import ConfiguracionInvalida, TemplateInvalido
from biometria.extractores import ExtractorArcFace, crear_extractor
from biometria.extractores.arcface import MODELO_POR_DEFECTO
from biometria.web.analisis import retrato
from tests.conftest import cfg_con

import cv2  # noqa: I001

pytestmark = pytest.mark.skipif(not (RAIZ_REPO / MODELO_POR_DEFECTO).is_file(),
                                reason="Falta el modelo ArcFace: python scripts/descargar_modelos.py")


@pytest.fixture(scope="module")
def arcface():
    return ExtractorArcFace()


@pytest.fixture(scope="module")
def cara(detector, astronauta):
    return detector.procesar(astronauta).cara


def test_template_unitario_de_512(arcface, cara):
    t = arcface.extraer(cara)
    assert t.shape == (512,) and t.dtype == np.float32
    assert np.linalg.norm(t) == pytest.approx(1.0, abs=1e-5)
    assert arcface.comparar(t, t) == pytest.approx(1.0, abs=1e-5)


def test_extraer_es_determinista_y_acepta_gris(arcface, cara):
    assert np.array_equal(arcface.extraer(cara), arcface.extraer(cara))
    gris = cv2.cvtColor(cara, cv2.COLOR_BGR2GRAY)
    assert arcface.comparar(arcface.extraer(cara), arcface.extraer(gris)) > 0.8


def test_misma_cara_puntua_alto_y_una_textura_cerca_de_0(arcface, detector, astronauta, cara):
    alto, ancho = astronauta.shape[:2]
    girada = cv2.warpAffine(astronauta, cv2.getRotationMatrix2D((ancho / 2, alto / 2), 10, 1.0),
                            (ancho, alto), borderMode=cv2.BORDER_REFLECT)
    ruido = np.random.default_rng(0).integers(0, 256, cara.shape, dtype=np.uint8)
    t = arcface.extraer(cara)
    misma = arcface.comparar(t, arcface.extraer(detector.procesar(girada).cara))
    otra = arcface.comparar(t, arcface.extraer(ruido))
    assert misma > 0.8 and otra < 0.15


def test_voltear_la_cara_apenas_cambia_el_template(arcface, cara):
    # Con voltear=True se suman la cara y su espejo: el template es el mismo para ambas
    assert arcface.comparar(arcface.extraer(cara), arcface.extraer(cara[:, ::-1])) > 0.99


def test_similitud_es_coseno_recortado_a_0():
    a = np.zeros(512, dtype=np.float32)
    b = np.zeros(512, dtype=np.float32)
    a[0] = b[1] = 1.0
    extractor = ExtractorArcFace.__new__(ExtractorArcFace)  # sin cargar la red
    assert extractor.comparar(a, b) == 0.0
    assert extractor.comparar(a, -a) == 0.0  # opuestos: "nada en común", no negativo
    c = np.zeros(512, dtype=np.float32)
    c[0] = c[1] = np.sqrt(0.5)
    assert extractor.comparar(a, c) == pytest.approx(np.sqrt(0.5), abs=1e-6)


def test_valida_norma_y_dimension(arcface):
    with pytest.raises(TemplateInvalido, match="norma"):
        arcface.validar_template(np.ones(512))
    with pytest.raises(TemplateInvalido):
        arcface.validar_template(np.zeros(128))  # un template SFace no vale


def test_desde_config(cfg):
    extractor = crear_extractor(cfg_con(cfg, extractor={"nombre": "arcface", "parametros": {"voltear": False}}))
    assert extractor.dimension == 512 and extractor.voltear is False
    with pytest.raises(ConfiguracionInvalida, match="112"):
        crear_extractor(cfg_con(cfg, preprocesado={"tam_cara": (96, 96)},
                                extractor={"nombre": "arcface", "parametros": {}}))
    with pytest.raises(ConfiguracionInvalida, match="voltear"):
        crear_extractor(cfg_con(cfg, extractor={"nombre": "arcface", "parametros": {"voltear": "si"}}))


def test_modelo_inexistente(cfg, tmp_path):
    with pytest.raises(ConfiguracionInvalida, match="no existe el modelo"):
        crear_extractor(cfg_con(cfg, extractor={"nombre": "arcface",
                                                "parametros": {"modelo": str(tmp_path / "x.onnx")}}))


def test_la_migracion_recupera_la_cara_del_retrato(arcface, detector, astronauta, cara):
    """scripts/migrar_extractor.py vuelve a extraer desde el retrato de la galería:
    tanto detectando la cara como con la posición conocida de los 5 puntos."""
    sys.path.insert(0, str(RAIZ_REPO / "scripts"))
    from migrar_extractor import LADO_RETRATO, landmarks_del_retrato
    from biometria.detector import alinear

    foto = retrato(astronauta, detector.procesar(astronauta).deteccion.landmarks)
    original = arcface.extraer(cara)
    detectada = detector.procesar(foto).cara
    conocida = alinear(foto, landmarks_del_retrato(LADO_RETRATO), (112, 112))
    assert arcface.comparar(original, arcface.extraer(detectada)) > 0.9
    assert arcface.comparar(original, arcface.extraer(conocida)) > 0.9
