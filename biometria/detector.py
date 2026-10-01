"""Detección de caras (YuNet), alineado y control de calidad.

Flujo completo (`Detector.procesar`), usado por inscribir/verificar/identificar:

    imagen BGR ──► detectar (YuNet) ──► elegir cara ──► ¿cara pequeña?
               ──► alinear con 5 landmarks ──► ¿borrosa? ──► cara alineada (tam_cara)

Flujo sin detector (`Detector.preparar_sin_detector`), usado por los métodos _sd:

    imagen BGR (se asume que YA es una cara) ──► redimensionar ──► ¿borrosa? ──► cara

Toda puntuación de confianza que devuelve este módulo está en [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from biometria.config import Config
from biometria.errores import (
    CaraPequena,
    ConfiguracionInvalida,
    ImagenBorrosa,
    SinCara,
    VariasCaras,
)

# Posición de referencia de los 5 landmarks en una cara de 112x112 (plantilla
# estándar de ArcFace/InsightFace). Orden: ojo izquierdo de la imagen, ojo
# derecho de la imagen, punta de la nariz, comisura izquierda, comisura derecha.
# YuNet devuelve los landmarks en este mismo orden.
PLANTILLA_LANDMARKS_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

# Las imágenes con un lado mayor que este se reducen SOLO para detectar
# (YuNet es mucho más lento en imágenes grandes). Las coordenadas se
# reescalan después a la imagen original, de donde se recorta la cara.
MAX_LADO_DETECCION = 1280

# YuNet pierde las caras que ocupan más del ~40 % del ancho de su entrada
# (p. ej. un retrato de primer plano). Por eso se detecta también sobre copias
# más pequeñas, donde esa misma cara ya tiene un tamaño que la red reconoce, y
# se combinan los resultados. Lado mayor de cada copia:
LADOS_DETECCION = (MAX_LADO_DETECCION, 640, 320)
# Dos detecciones de escalas distintas con más solapamiento (IoU) que esto son
# la misma cara: se queda la de mayor confianza.
IOU_MISMA_CARA = 0.4


@dataclass(frozen=True)
class Deteccion:
    """Una cara detectada, en coordenadas de la imagen original."""

    caja: tuple[int, int, int, int]  # (x, y, ancho, alto)
    landmarks: np.ndarray            # (5, 2) float32, mismo orden que la plantilla
    confianza: float                 # puntuación de YuNet en [0, 1]

    @property
    def area(self) -> int:
        return max(self.caja[2], 0) * max(self.caja[3], 0)


@dataclass(frozen=True)
class CaraProcesada:
    """Resultado del preprocesado, listo para el extractor."""

    cara: np.ndarray                 # BGR uint8 de tamaño tam_cara
    deteccion: Deteccion | None      # None si se omitió el detector (_sd)
    nitidez: float                   # varianza del Laplaciano de la cara


def nitidez(cara_bgr: np.ndarray) -> float:
    """Varianza del Laplaciano: valores bajos = imagen borrosa o sin textura."""
    gris = cv2.cvtColor(cara_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gris, cv2.CV_64F).var())


def matriz_alineado(landmarks: np.ndarray, tam: tuple[int, int]) -> np.ndarray:
    """Transformación de similitud (rotación + escala + traslación) que lleva
    los landmarks detectados a la plantilla, escalada a `tam` (ancho, alto)."""
    ancho, alto = tam
    destino = PLANTILLA_LANDMARKS_112 * np.array([ancho / 112.0, alto / 112.0], dtype=np.float32)
    matriz, _ = cv2.estimateAffinePartial2D(
        landmarks.astype(np.float32), destino, method=cv2.LMEDS
    )
    if matriz is None:
        raise SinCara("No se pudo alinear la cara (landmarks degenerados)")
    return matriz


def alinear(imagen: np.ndarray, landmarks: np.ndarray, tam: tuple[int, int]) -> np.ndarray:
    """Recorta y alinea la cara: ojos horizontales y a altura fija, tamaño `tam`.

    Se usan los 5 landmarks (no solo los ojos) porque la estimación es más
    estable ante errores de un único punto; los ojos dominan la rotación.
    """
    matriz = matriz_alineado(landmarks, tam)
    return cv2.warpAffine(
        imagen, matriz, tam, flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0),
    )


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersección sobre unión de dos cajas (x, y, ancho, alto)."""
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    interseccion = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - interseccion
    return interseccion / union if union > 0 else 0.0


class Detector:
    """Envoltorio de YuNet con alineado y control de calidad configurables."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        ruta = cfg.detector.modelo
        if not ruta.is_file():
            raise ConfiguracionInvalida(
                f"No se encuentra el modelo del detector: {ruta}. "
                "Ejecuta: python scripts/descargar_modelos.py"
            )
        self._yunet = cv2.FaceDetectorYN.create(
            str(ruta), "", (320, 320),
            cfg.detector.umbral_confianza, cfg.detector.umbral_nms, 5000,
        )

    # ------------------------------------------------------------------ #
    # Detección
    # ------------------------------------------------------------------ #
    def detectar(self, imagen: np.ndarray, umbral_confianza: float | None = None) -> list[Deteccion]:
        """Detecta todas las caras de una imagen BGR.

        Args:
            imagen: imagen BGR uint8 (ya validada con biometria.imagen.cargar_imagen).
            umbral_confianza: confianza mínima en [0, 1]. Si es None se usa la de config.

        Returns:
            Lista de detecciones ordenada de mayor a menor área (puede estar vacía).
        """
        umbral = self.cfg.detector.umbral_confianza if umbral_confianza is None else umbral_confianza
        if not 0.0 <= umbral <= 1.0:
            raise ValueError("umbral_confianza debe estar en [0, 1]")

        alto, ancho = imagen.shape[:2]
        escalas = sorted({min(1.0, lado / max(alto, ancho)) for lado in LADOS_DETECCION}, reverse=True)
        candidatas: list[Deteccion] = []
        for escala in escalas:
            candidatas.extend(self._detectar_a_escala(imagen, escala, umbral))

        # Fusión entre escalas: de mayor a menor confianza, se descarta lo que
        # se solapa con una cara ya aceptada.
        detecciones: list[Deteccion] = []
        for candidata in sorted(candidatas, key=lambda d: d.confianza, reverse=True):
            if all(iou(candidata.caja, d.caja) <= IOU_MISMA_CARA for d in detecciones):
                detecciones.append(candidata)
        detecciones.sort(key=lambda d: d.area, reverse=True)
        return detecciones

    def _detectar_a_escala(self, imagen: np.ndarray, escala: float, umbral: float) -> list[Deteccion]:
        """Pasa YuNet por una copia reescalada y devuelve las caras en coordenadas originales."""
        alto, ancho = imagen.shape[:2]
        entrada = imagen
        if escala < 1.0:
            entrada = cv2.resize(imagen, (max(1, round(ancho * escala)), max(1, round(alto * escala))),
                                 interpolation=cv2.INTER_AREA)
        self._yunet.setInputSize((entrada.shape[1], entrada.shape[0]))
        self._yunet.setScoreThreshold(umbral)
        _, caras = self._yunet.detect(entrada)
        if caras is None:
            return []
        detecciones = []
        for fila in caras:
            # Formato YuNet: x, y, w, h, 5 landmarks (x, y), puntuación
            caja = fila[0:4] / escala
            landmarks = (fila[4:14].reshape(5, 2) / escala).astype(np.float32)
            confianza = float(np.clip(fila[14], 0.0, 1.0))
            detecciones.append(Deteccion(
                caja=tuple(int(round(v)) for v in caja),
                landmarks=landmarks,
                confianza=confianza,
            ))
        return detecciones

    def elegir_cara(self, detecciones: list[Deteccion]) -> Deteccion:
        """Aplica la política de config ante 0 o varias caras."""
        if not detecciones:
            raise SinCara("No se ha detectado ninguna cara en la imagen")
        if len(detecciones) > 1 and self.cfg.detector.varias_caras == "error":
            raise VariasCaras(
                f"Se han detectado {len(detecciones)} caras; la imagen debe contener solo una"
            )
        return detecciones[0]  # ya ordenadas por área: la mayor

    # ------------------------------------------------------------------ #
    # Preprocesado
    # ------------------------------------------------------------------ #
    def procesar_deteccion(self, imagen: np.ndarray, deteccion: Deteccion) -> CaraProcesada:
        """Control de calidad + alineado de una detección concreta."""
        lado = min(deteccion.caja[2], deteccion.caja[3])
        if lado < self.cfg.calidad.tam_min_cara:
            raise CaraPequena(
                f"La cara mide {lado}px (mínimo {self.cfg.calidad.tam_min_cara}px)"
            )
        cara = alinear(imagen, deteccion.landmarks, self.cfg.preprocesado.tam_cara)
        return self._comprobar_nitidez(cara, deteccion)

    def procesar(self, imagen: np.ndarray) -> CaraProcesada:
        """Pipeline completo: detectar, elegir cara, calidad y alineado."""
        deteccion = self.elegir_cara(self.detectar(imagen))
        return self.procesar_deteccion(imagen, deteccion)

    def preparar_sin_detector(self, imagen: np.ndarray) -> CaraProcesada:
        """Preprocesado de los métodos _sd: la imagen se toma como cara ya recortada.

        No hay landmarks, así que no se alinea: solo se redimensiona a tam_cara.
        El control de nitidez sí se aplica (es calidad, no detección).
        """
        tam = self.cfg.preprocesado.tam_cara
        reduce = imagen.shape[1] > tam[0] or imagen.shape[0] > tam[1]
        cara = cv2.resize(imagen, tam, interpolation=cv2.INTER_AREA if reduce else cv2.INTER_LINEAR)
        return self._comprobar_nitidez(cara, None)

    def _comprobar_nitidez(self, cara: np.ndarray, deteccion: Deteccion | None) -> CaraProcesada:
        valor = nitidez(cara)
        if valor < self.cfg.calidad.umbral_borrosidad:
            raise ImagenBorrosa(
                f"La cara está demasiado borrosa (nitidez {valor:.1f}, "
                f"mínimo {self.cfg.calidad.umbral_borrosidad})"
            )
        return CaraProcesada(cara=cara, deteccion=deteccion, nitidez=valor)
