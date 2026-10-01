"""Tests del detector YuNet, el alineado y el control de calidad."""

import numpy as np
import pytest

from biometria.camara import dibujar_detecciones, evaluar_frame
from biometria.detector import Detector, matriz_alineado, nitidez
from biometria.errores import CaraPequena, ImagenBorrosa, SinCara, VariasCaras
from tests.conftest import cfg_con

import cv2  # noqa: I001


def _girar(imagen, grados):
    alto, ancho = imagen.shape[:2]
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), grados, 1.0)
    return cv2.warpAffine(imagen, m, (ancho, alto), borderMode=cv2.BORDER_REFLECT)


# --- Detección ---------------------------------------------------------------

def test_detecta_una_cara_con_confianza_entre_0_y_1(detector, astronauta):
    detecciones = detector.detectar(astronauta)
    assert len(detecciones) == 1
    det = detecciones[0]
    assert 0.0 <= det.confianza <= 1.0
    assert det.confianza > 0.8
    assert det.landmarks.shape == (5, 2)
    # El primer ojo es el de la izquierda de la imagen (orden de la plantilla)
    assert det.landmarks[0, 0] < det.landmarks[1, 0]


def test_umbral_de_confianza_por_llamada(detector, astronauta):
    assert detector.detectar(astronauta, umbral_confianza=1.0) == []
    with pytest.raises(ValueError):
        detector.detectar(astronauta, umbral_confianza=1.5)


def test_imagen_grande_se_reescala_a_coordenadas_originales(detector, astronauta):
    # Una imagen de 2048 px se detecta internamente a 1280 px (MAX_LADO_DETECCION).
    # Sus coordenadas deben salir en la escala de 2048, no en la de 1280.
    grande = cv2.resize(astronauta, (2048, 2048))
    det_grande = detector.detectar(grande)[0]
    det_1280 = detector.detectar(cv2.resize(astronauta, (1280, 1280)))[0]
    assert np.allclose(det_grande.landmarks, det_1280.landmarks * 2048 / 1280, atol=3)


# --- Pipeline completo y control de calidad ------------------------------------

def test_procesar_devuelve_cara_alineada(detector, astronauta, cfg):
    procesada = detector.procesar(astronauta)
    ancho, alto = cfg.preprocesado.tam_cara
    assert procesada.cara.shape == (alto, ancho, 3)
    assert procesada.cara.dtype == np.uint8
    assert procesada.nitidez >= cfg.calidad.umbral_borrosidad


def test_tam_cara_configurable(cfg, astronauta):
    det = Detector(cfg_con(cfg, preprocesado={"tam_cara": (64, 80)}))
    assert det.procesar(astronauta).cara.shape == (80, 64, 3)


def test_alineado_pone_los_ojos_horizontales(detector, astronauta, cfg):
    girada = _girar(astronauta, 20)
    det = detector.detectar(girada)[0]
    m = matriz_alineado(det.landmarks, cfg.preprocesado.tam_cara)
    ojos = cv2.transform(det.landmarks[None, :2], m)[0]
    assert abs(ojos[0, 1] - ojos[1, 1]) < 2.0  # misma altura tras alinear


def test_sin_cara(detector):
    ruido = np.random.default_rng(0).integers(0, 255, (400, 400, 3), dtype=np.uint8)
    with pytest.raises(SinCara):
        detector.procesar(ruido)
    with pytest.raises(SinCara):
        detector.procesar(np.full((400, 400, 3), 128, np.uint8))


def test_varias_caras_error_o_mayor(cfg, detector, astronauta):
    pequena = cv2.resize(astronauta, (384, 384))
    pequena = cv2.copyMakeBorder(pequena, 64, 64, 0, 0, cv2.BORDER_CONSTANT)
    dos = np.hstack([astronauta, pequena])
    with pytest.raises(VariasCaras):
        detector.procesar(dos)

    det_mayor = Detector(cfg_con(cfg, detector={"varias_caras": "mayor"}))
    procesada = det_mayor.procesar(dos)
    assert procesada.deteccion.caja[0] < 512  # la de la izquierda, la grande


def test_cara_pequena(cfg, astronauta):
    det = Detector(cfg_con(cfg, calidad={"tam_min_cara": 200}))
    with pytest.raises(CaraPequena):
        det.procesar(astronauta)


def test_imagen_borrosa(cfg, detector, astronauta):
    borrosa = cv2.GaussianBlur(astronauta, (0, 0), 4)
    permisivo = Detector(cfg_con(cfg, calidad={"umbral_borrosidad": 0.0}))
    # La nitidez medida baja claramente al desenfocar...
    assert permisivo.procesar(borrosa).nitidez < permisivo.procesar(astronauta).nitidez / 3
    # ...y con un umbral exigente se rechaza
    estricto = Detector(cfg_con(cfg, calidad={"umbral_borrosidad": 1e6}))
    with pytest.raises(ImagenBorrosa):
        estricto.procesar(astronauta)


# --- Modo sin detector (_sd) -----------------------------------------------------

def test_preparar_sin_detector(detector, cfg, astronauta):
    procesada = detector.preparar_sin_detector(astronauta)
    ancho, alto = cfg.preprocesado.tam_cara
    assert procesada.cara.shape == (alto, ancho, 3)
    assert procesada.deteccion is None


def test_sin_detector_rechaza_imagen_plana(detector):
    with pytest.raises(ImagenBorrosa):
        detector.preparar_sin_detector(np.full((200, 200, 3), 90, np.uint8))


def test_nitidez_de_imagen_plana_es_cero():
    assert nitidez(np.full((50, 50, 3), 7, np.uint8)) == 0.0


# --- Utilidades de la cámara (sin cámara real) -----------------------------------

def test_evaluar_frame_y_dibujar(detector, astronauta):
    detecciones, procesada, estado = evaluar_frame(detector, astronauta, 0.5)
    assert procesada is not None and estado.startswith("OK")
    vista = dibujar_detecciones(astronauta, detecciones, estado)
    assert vista.shape == astronauta.shape
    assert not np.array_equal(vista, astronauta)  # se ha dibujado algo


def test_evaluar_frame_sin_caras(detector):
    detecciones, procesada, estado = evaluar_frame(detector, np.zeros((240, 320, 3), np.uint8), None)
    assert detecciones == [] and procesada is None and estado == ""


def test_detecta_caras_muy_grandes(detector, astronauta):
    # Un primer plano en el que la cara ocupa ~70 % del ancho: YuNet a una sola
    # escala no la ve; la detección multiescala sí.
    x, y, w, h = detector.detectar(astronauta)[0].caja
    lado = int(w / 0.7)
    cx, cy = x + w // 2, y + h // 2
    borde = cv2.copyMakeBorder(astronauta, lado, lado, lado, lado, cv2.BORDER_REFLECT)
    primer_plano = borde[cy + lado - lado // 2: cy + lado + lado // 2, cx + lado - lado // 2: cx + lado + lado // 2]
    grande = cv2.resize(primer_plano, (2560, 2560))
    detecciones = detector.detectar(grande)
    assert len(detecciones) == 1
    assert detecciones[0].caja[2] > 2560 * 0.5
