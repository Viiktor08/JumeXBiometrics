"""Pipeline paso a paso para la web y galería de retratos.

`analizar` hace lo mismo que `SistemaBiometrico.extraer`, pero guarda cada paso
intermedio (detecciones, cara alineada, template, tiempos) para poder
enseñarlo en la interfaz. Después la web llama a los métodos `_t` del sistema
con el template obtenido, así que la decisión es exactamente la del sistema.

La galería guarda un retrato de cada persona inscrita SOLO para mostrarlo en
la web. El sistema biométrico no lo usa: compara siempre templates.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from biometria.bd import validar_id
from biometria.detector import PLANTILLA_LANDMARKS_112, CaraProcesada, Deteccion
from biometria.errores import ErrorBiometrico
from biometria.imagen import cargar_imagen
from biometria.sistema import SistemaBiometrico

LADO_RETRATO = 256
# Fracción del retrato que ocupa la plantilla de landmarks: < 1 deja margen
# alrededor de la cara (la cara alineada de 112x112 va muy justa).
ESCALA_RETRATO = 0.62


@dataclass
class Analisis:
    """Resultado de cada paso. Los campos quedan a None desde el paso que falló."""

    imagen: np.ndarray | None = None
    detecciones: list[Deteccion] = field(default_factory=list)
    procesada: CaraProcesada | None = None
    template: np.ndarray | None = None
    error: ErrorBiometrico | None = None
    tiempos_ms: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.template is not None


def analizar(sistema: SistemaBiometrico, foto) -> Analisis:
    """Carga ► detecta ► elige cara ► calidad y alineado ► extrae, cronometrando cada paso."""
    analisis = Analisis()
    reloj = time.perf_counter()

    def marcar(paso: str) -> None:
        nonlocal reloj
        ahora = time.perf_counter()
        analisis.tiempos_ms[paso] = round((ahora - reloj) * 1000, 1)
        reloj = ahora

    try:
        analisis.imagen = cargar_imagen(foto, sistema.cfg.limites)
        marcar("carga")
        analisis.detecciones = sistema.detector.detectar(analisis.imagen)
        marcar("deteccion")
        elegida = sistema.detector.elegir_cara(analisis.detecciones)
        analisis.procesada = sistema.detector.procesar_deteccion(analisis.imagen, elegida)
        marcar("alineado")
        analisis.template = sistema.extractor.extraer(analisis.procesada.cara)
        marcar("extraccion")
    except ErrorBiometrico as e:
        analisis.error = e
    return analisis


def retrato(imagen: np.ndarray, landmarks: np.ndarray, lado: int = LADO_RETRATO) -> np.ndarray:
    """Recorte cuadrado y alineado con margen, para mostrar (no para reconocer)."""
    destino = (PLANTILLA_LANDMARKS_112 / 112.0 - 0.5) * ESCALA_RETRATO * lado + lado / 2
    destino[:, 1] += lado * 0.04  # un poco más abajo: que se vea algo de pelo
    matriz, _ = cv2.estimateAffinePartial2D(landmarks.astype(np.float32), destino.astype(np.float32),
                                            method=cv2.LMEDS)
    if matriz is None:
        return cv2.resize(imagen, (lado, lado))
    return cv2.warpAffine(imagen, matriz, (lado, lado), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def a_data_url(imagen: np.ndarray, formato: str = ".png") -> str:
    ok, datos = cv2.imencode(formato, imagen, [cv2.IMWRITE_JPEG_QUALITY, 90] if formato == ".jpg" else [])
    if not ok:
        raise ValueError("no se pudo codificar la imagen")
    tipo = "image/jpeg" if formato == ".jpg" else "image/png"
    return f"data:{tipo};base64," + base64.b64encode(datos.tobytes()).decode("ascii")


class Galeria:
    """Retratos de las personas inscritas, uno por persona, en <carpeta>/<hash>.jpg.

    El nombre del fichero es un hash del id: el id nunca forma parte de una ruta,
    así que no hay forma de salirse de la carpeta con un id malicioso.
    """

    def __init__(self, carpeta: Path):
        self.carpeta = carpeta

    @staticmethod
    def clave(id_persona: str) -> str:
        return hashlib.sha256(validar_id(id_persona).encode("utf-8")).hexdigest()[:20]

    def ruta(self, id_persona: str) -> Path:
        return self.carpeta / f"{self.clave(id_persona)}.jpg"

    def existe(self, id_persona: str) -> bool:
        return self.ruta(id_persona).is_file()

    def guardar(self, id_persona: str, imagen: np.ndarray, sobrescribir: bool = False) -> bool:
        """Guarda el retrato si no había uno (o si se pide sobrescribir)."""
        destino = self.ruta(id_persona)
        if destino.is_file() and not sobrescribir:
            return False
        self.carpeta.mkdir(parents=True, exist_ok=True)
        ok, datos = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            temporal = destino.with_suffix(".tmp")
            temporal.write_bytes(datos.tobytes())
            temporal.replace(destino)
        return bool(ok)

    def guardar_de_analisis(self, id_persona: str, analisis: Analisis) -> bool:
        if analisis.imagen is None or analisis.procesada is None or analisis.procesada.deteccion is None:
            return False
        return self.guardar(id_persona, retrato(analisis.imagen, analisis.procesada.deteccion.landmarks))

    def ruta_por_clave(self, clave: str) -> Path | None:
        """Ruta del retrato a partir de la clave de la URL (solo hex de 20)."""
        if len(clave) != 20 or any(c not in "0123456789abcdef" for c in clave):
            return None
        ruta = self.carpeta / f"{clave}.jpg"
        return ruta if ruta.is_file() else None
