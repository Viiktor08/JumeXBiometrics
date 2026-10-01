"""Fixtures comunes de los tests.

Imágenes de prueba: se usa el retrato oficial de la astronauta Eileen Collins
(NASA, dominio público), distribuido con scikit-image. Se descarga una vez a
tests/.cache/ y se verifica su SHA256. A partir de ella se generan los casos
difíciles (dos caras, cara pequeña, borrosa, girada...).
"""

import hashlib
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

# biometria se importa antes que cv2 (fija el límite de píxeles del decodificador)
from biometria.config import cargar_config
from biometria.detector import Detector

import cv2  # noqa: I001

CACHE = Path(__file__).resolve().parent / ".cache"

URL_ASTRONAUTA = (
    "https://raw.githubusercontent.com/scikit-image/scikit-image/"
    "v0.24.0/skimage/data/astronaut.png"
)
SHA256_ASTRONAUTA = "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5"


def _descargar_verificado(url: str, destino: Path, sha256: str) -> Path:
    """Descarga (si hace falta) y verifica el hash; si no hay red, salta el test."""
    if destino.is_file() and hashlib.sha256(destino.read_bytes()).hexdigest() == sha256:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, destino)
    except OSError as e:
        pytest.skip(f"No se pudo descargar la imagen de prueba ({e})")
    if hashlib.sha256(destino.read_bytes()).hexdigest() != sha256:
        destino.unlink()
        pytest.fail(f"Hash incorrecto en la imagen de prueba descargada de {url}")
    return destino


@pytest.fixture(scope="session")
def cfg():
    return cargar_config()


def cfg_con(cfg, **secciones):
    """Copia de la configuración cambiando campos: cfg_con(cfg, detector={"varias_caras": "mayor"})"""
    cambios = {nombre: replace(getattr(cfg, nombre), **campos) for nombre, campos in secciones.items()}
    return replace(cfg, **cambios)


@pytest.fixture(scope="session")
def detector(cfg):
    if not cfg.detector.modelo.is_file():
        pytest.skip("Falta el modelo YuNet: ejecuta python scripts/descargar_modelos.py")
    return Detector(cfg)


@pytest.fixture(scope="session")
def ruta_astronauta():
    return _descargar_verificado(URL_ASTRONAUTA, CACHE / "astronaut.png", SHA256_ASTRONAUTA)


@pytest.fixture(scope="session")
def astronauta(ruta_astronauta):
    """Foto BGR 512x512 con una única cara frontal."""
    return cv2.imread(str(ruta_astronauta), cv2.IMREAD_COLOR)
