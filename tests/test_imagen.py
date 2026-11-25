"""Tests de la carga segura de imágenes (límites y formatos)."""

import numpy as np
import pytest

from biometria.errores import ImagenDemasiadoGrande, ImagenInvalida
from biometria.imagen import cargar_imagen
from tests.conftest import cfg_con

import cv2  # noqa: I001


def test_carga_desde_ruta_y_bytes(cfg, ruta_astronauta):
    desde_ruta = cargar_imagen(ruta_astronauta, cfg.limites)
    desde_bytes = cargar_imagen(ruta_astronauta.read_bytes(), cfg.limites)
    assert desde_ruta.shape == (512, 512, 3) and desde_ruta.dtype == np.uint8
    assert np.array_equal(desde_ruta, desde_bytes)


def test_fichero_inexistente(cfg, tmp_path):
    with pytest.raises(ImagenInvalida, match="No existe"):
        cargar_imagen(tmp_path / "nada.jpg", cfg.limites)


def test_bytes_que_no_son_imagen(cfg, tmp_path):
    falso = tmp_path / "falso.jpg"
    falso.write_bytes(b"esto no es un jpeg" * 100)
    with pytest.raises(ImagenInvalida):
        cargar_imagen(falso, cfg.limites)
    with pytest.raises(ImagenInvalida):
        cargar_imagen(b"", cfg.limites)


def test_limite_de_bytes(cfg, ruta_astronauta):
    limites = cfg_con(cfg, limites={"max_bytes_imagen": 1000}).limites
    with pytest.raises(ImagenDemasiadoGrande):
        cargar_imagen(ruta_astronauta, limites)
    with pytest.raises(ImagenDemasiadoGrande):
        cargar_imagen(ruta_astronauta.read_bytes(), limites)


def test_limite_de_pixeles_configurable(cfg, astronauta):
    limites = cfg_con(cfg, limites={"max_pixeles": 100_000}).limites  # 512*512 > 100k
    with pytest.raises(ImagenDemasiadoGrande):
        cargar_imagen(astronauta, limites)


def test_bomba_de_descompresion(cfg):
    """PNG de 8000x8000 (64 Mpx) que pesa muy poco: debe rechazarse SIN decodificarlo."""
    ok, png = cv2.imencode(".png", np.zeros((8000, 8000), np.uint8))
    assert ok and len(png) < cfg.limites.max_bytes_imagen
    with pytest.raises(ImagenDemasiadoGrande):
        cargar_imagen(png.tobytes(), cfg.limites)


@pytest.mark.parametrize("forma", [(64, 64), (64, 64, 1), (64, 64, 4)])
def test_arrays_se_normalizan_a_bgr(cfg, forma):
    imagen = cargar_imagen(np.zeros(forma, np.uint8), cfg.limites)
    assert imagen.shape == (64, 64, 3)


@pytest.mark.parametrize("array", [
    np.zeros((64, 64, 3), np.float32),   # tipo incorrecto
    np.zeros((64, 64, 2), np.uint8),     # nº de canales raro
    np.zeros((8, 8, 3), np.uint8),       # demasiado pequeña
    np.zeros(100, np.uint8),             # 1D
])
def test_arrays_invalidos(cfg, array):
    with pytest.raises(ImagenInvalida):
        cargar_imagen(array, cfg.limites)


def test_tipo_no_soportado(cfg):
    with pytest.raises(ImagenInvalida):
        cargar_imagen(12345, cfg.limites)
