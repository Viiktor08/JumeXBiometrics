"""Extractor ArcFace (InsightFace, ResNet-50). TÉCNICA ASIGNADA.

ArcFace (Deng et al., 2019, "ArcFace: Additive Angular Margin Loss for Deep
Face Recognition") es una forma de ENTRENAR una red de reconocimiento facial.
Durante el entrenamiento, cada persona tiene un vector "centro" en una
hiperesfera y la red aprende a llevar sus caras hacia él; la pérdida añade un
margen angular m al ángulo con el centro correcto:

    logit de la clase correcta = s · cos(θ + m)     (s = 64, m = 0.5 rad)

Así la red no se conforma con que la cara "caiga del lado bueno": tiene que
quedar a más de m radianes de cualquier otra persona. El resultado son vectores
muy compactos por persona y bien separados entre personas.

En reconocimiento solo se usa la red ya entrenada (el modelo `w600k_r50` de
InsightFace: ResNet-50 entrenada con WebFace600K, ~600 000 personas):

    cara BGR 112x112 ─► RGB, (x − 127.5) / 127.5 ─► ResNet-50 ─► vector 512
    (y lo mismo con la cara volteada) ─► suma ─► normalizar (norma 1) ─► template

Voltear la cara y sumar los dos vectores (test-time augmentation) es habitual
al evaluar ArcFace: compensa pequeñas asimetrías de luz o pose.

La cara llega ya alineada por `Detector.procesar` con la misma plantilla de
5 puntos con la que se entrenó ArcFace, así que no hace falta nada más.

Comparación: similitud coseno c = a·b (son vectores de norma 1). Con ArcFace,
dos personas distintas dan c ≈ 0 (vectores casi perpendiculares) y la misma
persona c ≈ 0.4–0.9. Los valores negativos solo significan "nada en común",
así que la similitud es max(c, 0), en [0, 1]: un desconocido sale cerca del
0 % en vez de en el 50 % que daría (c + 1) / 2.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from biometria.config import RAIZ_REPO
from biometria.errores import TemplateInvalido
from biometria.extractores.base import Extractor

MODELO_POR_DEFECTO = "models/arcface_w600k_r50.onnx"
TAM_ENTRADA = (112, 112)
MEDIA = 127.5
ESCALA = 1.0 / 127.5
# Tolerancia de la norma de un template (cubre el redondeo de float32)
TOLERANCIA_NORMA = 1e-3


class ExtractorArcFace(Extractor):
    nombre = "arcface"
    dimension = 512

    def __init__(self, modelo: str = MODELO_POR_DEFECTO, voltear: bool = True):
        """
        Args:
            modelo: ruta del ONNX (relativa a la raíz del repositorio o absoluta).
            voltear: suma también el vector de la cara volteada (más robusto, el doble de lento).
        """
        if not isinstance(voltear, bool):
            raise ValueError("voltear debe ser true o false")
        ruta = Path(modelo)
        self.modelo = ruta if ruta.is_absolute() else RAIZ_REPO / ruta
        if not self.modelo.is_file():
            raise ValueError(
                f"no existe el modelo {self.modelo}: ejecuta python scripts/descargar_modelos.py"
            )
        self.voltear = voltear
        self._red = cv2.dnn.readNetFromONNX(str(self.modelo))

    def comprobar_tam_cara(self, tam: tuple[int, int]) -> None:
        if tuple(tam) != TAM_ENTRADA:
            raise ValueError(f"ArcFace necesita preprocesado.tam_cara = [112, 112] (hay {list(tam)})")

    def _vectores(self, caras: list[np.ndarray]) -> np.ndarray:
        """Pasa un lote de caras BGR 112x112 por la red: (n, 512)."""
        lote = cv2.dnn.blobFromImages(caras, ESCALA, TAM_ENTRADA, (MEDIA, MEDIA, MEDIA), swapRB=True)
        self._red.setInput(lote)
        return self._red.forward().astype(np.float64)

    def extraer(self, cara: np.ndarray) -> np.ndarray:
        if cara.ndim == 2:
            cara = cv2.cvtColor(cara, cv2.COLOR_GRAY2BGR)
        if cara.shape[:2] != TAM_ENTRADA[::-1]:
            # Los métodos _sd con otro tam_cara no llegan aquí (comprobar_tam_cara),
            # pero así extraer() también acepta cualquier recorte.
            cara = cv2.resize(cara, TAM_ENTRADA, interpolation=cv2.INTER_AREA)
        caras = [np.ascontiguousarray(cara)]
        if self.voltear:
            caras.append(np.ascontiguousarray(cara[:, ::-1]))
        vector = self._vectores(caras).sum(axis=0)
        norma = np.linalg.norm(vector)
        if norma == 0 or not np.isfinite(norma):
            raise TemplateInvalido("ArcFace ha devuelto un vector nulo")
        return (vector / norma).astype(np.float32)

    def _similitud(self, a: np.ndarray, b: np.ndarray) -> float:
        coseno = float(np.dot(a.astype(np.float64), b.astype(np.float64)))
        return max(coseno, 0.0)

    def _validar_valores(self, template: np.ndarray) -> None:
        norma = float(np.linalg.norm(template.astype(np.float64)))
        if abs(norma - 1.0) > TOLERANCIA_NORMA:
            raise TemplateInvalido(f"Un template ArcFace debe tener norma 1 (tiene {norma:.4f})")

    def __repr__(self) -> str:
        return (f"ExtractorArcFace(modelo={self.modelo.name!r}, voltear={self.voltear}, "
                f"dimension={self.dimension})")
