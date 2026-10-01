"""Carga segura de imágenes.

Toda imagen que entra al sistema pasa por `cargar_imagen`, venga de un fichero,
de bytes (p. ej. subida por red) o de un array de numpy (p. ej. un frame de la
cámara). Así los límites de tamaño se aplican siempre en un único sitio.

Devuelve siempre una imagen BGR uint8 de 3 canales (el formato de OpenCV).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from biometria.config import ConfigLimites
from biometria.errores import ImagenDemasiadoGrande, ImagenInvalida

# Lado mínimo aceptado: por debajo no hay información útil de una cara.
LADO_MINIMO = 16

OrigenImagen = str | Path | bytes | np.ndarray


def _leer_fichero(ruta: Path, max_bytes: int) -> bytes:
    """Lee un fichero sin pasar del límite de bytes."""
    if not ruta.is_file():
        raise ImagenInvalida(f"No existe el fichero de imagen: {ruta}")
    if ruta.stat().st_size > max_bytes:
        raise ImagenDemasiadoGrande(
            f"El fichero ocupa {ruta.stat().st_size} bytes (máximo {max_bytes})"
        )
    with ruta.open("rb") as f:
        # Leemos como mucho max_bytes + 1: si el fichero crece entre el stat()
        # y la lectura, lo detectamos igualmente.
        datos = f.read(max_bytes + 1)
    if len(datos) > max_bytes:
        raise ImagenDemasiadoGrande(f"El fichero supera el máximo de {max_bytes} bytes")
    return datos


def _decodificar(datos: bytes, max_bytes: int) -> np.ndarray:
    """Decodifica bytes de imagen (JPEG, PNG, ...) a BGR."""
    if len(datos) > max_bytes:
        raise ImagenDemasiadoGrande(f"La imagen ocupa {len(datos)} bytes (máximo {max_bytes})")
    if not datos:
        raise ImagenInvalida("La imagen está vacía")
    buffer = np.frombuffer(datos, dtype=np.uint8)
    try:
        # IMREAD_COLOR aplica además la orientación EXIF (fotos de móvil giradas).
        imagen = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except cv2.error as e:
        # OpenCV lanza excepción cuando la cabecera declara más píxeles que
        # OPENCV_IO_MAX_IMAGE_PIXELS (ver biometria/__init__.py).
        raise ImagenDemasiadoGrande(
            "La imagen declara unas dimensiones mayores que el límite del decodificador"
        ) from e
    if imagen is None:
        raise ImagenInvalida("No es una imagen válida o el formato no está soportado")
    return imagen


def _normalizar_array(imagen: np.ndarray) -> np.ndarray:
    """Valida un array y lo convierte a BGR uint8 de 3 canales."""
    if not isinstance(imagen, np.ndarray):
        raise ImagenInvalida("La imagen debe ser un array de numpy")
    if imagen.dtype != np.uint8:
        raise ImagenInvalida(f"La imagen debe ser uint8, no {imagen.dtype}")
    if imagen.ndim == 2:
        return cv2.cvtColor(imagen, cv2.COLOR_GRAY2BGR)
    if imagen.ndim == 3 and imagen.shape[2] == 3:
        return np.ascontiguousarray(imagen)
    if imagen.ndim == 3 and imagen.shape[2] == 4:
        return cv2.cvtColor(imagen, cv2.COLOR_BGRA2BGR)
    if imagen.ndim == 3 and imagen.shape[2] == 1:
        return cv2.cvtColor(imagen, cv2.COLOR_GRAY2BGR)
    raise ImagenInvalida(f"Forma de imagen no soportada: {imagen.shape}")


def cargar_imagen(origen: OrigenImagen, limites: ConfigLimites) -> np.ndarray:
    """Carga y valida una imagen desde ruta, bytes o array.

    Errores:
        ImagenInvalida         no existe, no es imagen, formato o forma no soportados
        ImagenDemasiadoGrande  supera max_bytes_imagen o max_pixeles
    """
    if isinstance(origen, (str, Path)):
        imagen = _decodificar(_leer_fichero(Path(origen), limites.max_bytes_imagen),
                              limites.max_bytes_imagen)
    elif isinstance(origen, (bytes, bytearray, memoryview)):
        imagen = _decodificar(bytes(origen), limites.max_bytes_imagen)
    elif isinstance(origen, np.ndarray):
        imagen = origen
    else:
        raise ImagenInvalida(f"Tipo de imagen no soportado: {type(origen).__name__}")

    # Comprobamos píxeles antes de cualquier conversión que copie el array.
    if imagen.ndim < 2:
        raise ImagenInvalida(f"Forma de imagen no soportada: {imagen.shape}")
    alto, ancho = imagen.shape[:2]
    if alto * ancho > limites.max_pixeles:
        raise ImagenDemasiadoGrande(
            f"La imagen tiene {ancho}x{alto} píxeles (máximo {limites.max_pixeles})"
        )
    if min(alto, ancho) < LADO_MINIMO:
        raise ImagenInvalida(f"La imagen es demasiado pequeña ({ancho}x{alto})")

    return _normalizar_array(imagen)
