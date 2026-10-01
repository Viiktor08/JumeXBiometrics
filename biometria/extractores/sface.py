"""Extractor SFace (red neuronal de OpenCV Zoo). REFERENCIA, no la técnica asignada.

SFace (Zhong et al., 2021, "SFace: Sigmoid-Constrained Hypersphere Loss for
Robust Face Recognition") es una red tipo MobileFaceNet que convierte una cara
alineada de 112x112 en un vector de 128 valores. Viene con OpenCV
(`cv2.FaceRecognizerSF`) y se alinea con la misma plantilla de ArcFace que usa
nuestro detector, así que recibe directamente la cara de `Detector.procesar`.

Sirve como referencia fuerte frente al LBP provisional (y para la demo web)
mientras no tengamos asignada la técnica definitiva.

    cara BGR 112x112 ──► SFace ──► vector 128 ──► normalizar (norma 1) ──► template

Comparación: similitud coseno c en [-1, 1], llevada a [0, 1] con (c + 1) / 2.
El umbral que recomienda OpenCV para SFace (coseno 0.363) equivale a 0.68.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from biometria.config import RAIZ_REPO
from biometria.errores import TemplateInvalido
from biometria.extractores.base import Extractor

MODELO_POR_DEFECTO = "models/face_recognition_sface_2021dec.onnx"
TAM_ENTRADA = (112, 112)
# Tolerancia de la norma de un template (cubre el redondeo de float32)
TOLERANCIA_NORMA = 1e-3


class ExtractorSFace(Extractor):
    nombre = "sface"
    dimension = 128

    def __init__(self, modelo: str = MODELO_POR_DEFECTO):
        """
        Args:
            modelo: ruta del ONNX (relativa a la raíz del repositorio o absoluta).
        """
        ruta = Path(modelo)
        self.modelo = ruta if ruta.is_absolute() else RAIZ_REPO / ruta
        if not self.modelo.is_file():
            raise ValueError(
                f"no existe el modelo {self.modelo}: ejecuta python scripts/descargar_modelos.py"
            )
        self._red = cv2.FaceRecognizerSF.create(str(self.modelo), "")

    def comprobar_tam_cara(self, tam: tuple[int, int]) -> None:
        if tuple(tam) != TAM_ENTRADA:
            raise ValueError(f"SFace necesita preprocesado.tam_cara = [112, 112] (hay {list(tam)})")

    def extraer(self, cara: np.ndarray) -> np.ndarray:
        if cara.ndim == 2:
            cara = cv2.cvtColor(cara, cv2.COLOR_GRAY2BGR)
        if cara.shape[:2] != TAM_ENTRADA[::-1]:
            # Los métodos _sd con otro tam_cara no llegan aquí (comprobar_tam_cara),
            # pero así extraer() también acepta cualquier recorte.
            cara = cv2.resize(cara, TAM_ENTRADA, interpolation=cv2.INTER_AREA)
        vector = self._red.feature(np.ascontiguousarray(cara)).astype(np.float64).ravel()
        norma = np.linalg.norm(vector)
        if norma == 0 or not np.isfinite(norma):
            raise TemplateInvalido("SFace ha devuelto un vector nulo")
        return (vector / norma).astype(np.float32)

    def _similitud(self, a: np.ndarray, b: np.ndarray) -> float:
        coseno = float(np.dot(a.astype(np.float64), b.astype(np.float64)))
        return (coseno + 1.0) / 2.0

    def _validar_valores(self, template: np.ndarray) -> None:
        norma = float(np.linalg.norm(template.astype(np.float64)))
        if abs(norma - 1.0) > TOLERANCIA_NORMA:
            raise TemplateInvalido(f"Un template SFace debe tener norma 1 (tiene {norma:.4f})")

    def __repr__(self) -> str:
        return f"ExtractorSFace(modelo={self.modelo.name!r}, dimension={self.dimension})"
