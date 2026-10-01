"""Interfaz común de los extractores de características.

Un extractor convierte la cara alineada (BGR uint8, tam_cara) en un template:
un vector float32 de dimensión fija. También sabe comparar dos templates y
comprobar que un template recibido desde fuera (métodos _t, base de datos) es
válido para él.

    cara alineada ──► extraer ──► template (float32, dimension)
    template A, template B ──► comparar ──► puntuación en [0, 1] (1 = idénticos)

Cambiar de técnica (LBP -> ArcFace, LDA...) es añadir una subclase y
registrarla en biometria/extractores/__init__.py; el resto del sistema solo
usa esta interfaz.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from biometria.errores import TemplateInvalido


class Extractor(ABC):
    """Clase base. Las subclases fijan `nombre` y `dimension`."""

    nombre: str = ""
    dimension: int = 0

    @abstractmethod
    def extraer(self, cara: np.ndarray) -> np.ndarray:
        """Cara alineada BGR uint8 -> template float32 de forma (dimension,)."""

    @abstractmethod
    def _similitud(self, a: np.ndarray, b: np.ndarray) -> float:
        """Similitud en [0, 1] entre dos templates ya validados."""

    def comprobar_tam_cara(self, tam: tuple[int, int]) -> None:
        """Lanza ValueError si el extractor no puede trabajar con caras de
        tamaño `tam` (ancho, alto). Por defecto acepta cualquier tamaño."""

    def _validar_valores(self, template: np.ndarray) -> None:
        """Restricciones propias de la técnica (rango, normalización...).

        Lanza TemplateInvalido si no se cumplen. Por defecto no hay ninguna.
        """

    def validar_template(self, template) -> np.ndarray:
        """Convierte a float32 1D y comprueba dimensión, valores finitos y las
        restricciones propias del extractor. Devuelve el template validado.

        Es la puerta de entrada de los métodos _t: lo que no pase aquí no llega
        a compararse con la base de datos.
        """
        try:
            arr = np.asarray(template, dtype=np.float32)
        except (TypeError, ValueError) as e:
            raise TemplateInvalido(f"El template no es un vector numérico: {e}") from e
        if arr.ndim != 1 or arr.shape[0] != self.dimension:
            raise TemplateInvalido(
                f"El template debe ser un vector de {self.dimension} valores "
                f"para el extractor '{self.nombre}' (recibido: forma {arr.shape})"
            )
        if not np.all(np.isfinite(arr)):
            raise TemplateInvalido("El template contiene NaN o infinitos")
        self._validar_valores(arr)
        return arr

    def comparar(self, a, b) -> float:
        """Puntuación de similitud en [0, 1] entre dos templates (1 = idénticos).

        Es simétrica: comparar(a, b) == comparar(b, a).
        """
        puntuacion = self._similitud(self.validar_template(a), self.validar_template(b))
        return float(min(max(puntuacion, 0.0), 1.0))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(nombre={self.nombre!r}, dimension={self.dimension})"
