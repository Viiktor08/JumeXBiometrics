"""Extractor LBP (Local Binary Patterns) uniforme por rejilla. LÍNEA BASE.

Es el extractor clásico de Ahonen et al. (2006), "Face Description with Local
Binary Patterns". Fue el extractor provisional antes de tener asignada la
técnica (ArcFace); se conserva como línea base clásica para comparar.

    cara BGR ──► gris ──► código LBP por píxel ──► patrones uniformes (P·(P-1)+3 clases)
             ──► rejilla de celdas ──► histograma normalizado por celda ──► concatenar

- Código LBP: se comparan P vecinos en un círculo de radio R con el píxel
  central (vecino >= centro -> 1). Los vecinos que no caen en un píxel exacto
  se interpolan bilinealmente.
- Patrones uniformes: los códigos con como mucho 2 transiciones 0/1 (bordes,
  esquinas, manchas) tienen cada uno su clase; el resto comparten una sola.
  Con P=8 son 58 + 1 = 59 clases.
- Cada celda se normaliza para sumar 1 y después se divide entre el número de
  celdas: el template completo suma 1 y todas las celdas pesan lo mismo.

Comparación: distancia chi-cuadrado, d = Σ (a-b)² / (a+b), que vale entre 0
(idénticos) y 2 (sin nada en común) porque ambos suman 1. La similitud es
1 - d/2, en [0, 1].

LBP no cambia ante cambios de brillo/contraste que conserven el orden de los
grises, así que no hace falta ecualizar la cara antes.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from biometria.errores import TemplateInvalido
from biometria.extractores.base import Extractor

# Tolerancia al comprobar que cada celda de un template suma lo que debe
# (cubre el redondeo de float32 y de la serialización a texto).
TOLERANCIA_SUMA = 1e-3


def _ajustar(valor: float) -> float:
    """Redondea al entero más cercano si está a menos de 1e-9 de él."""
    entero = round(valor)
    return float(entero) if abs(valor - entero) < 1e-9 else valor


def tabla_uniforme(puntos: int) -> np.ndarray:
    """Tabla código LBP (0 .. 2^P-1) -> clase uniforme (0 .. P·(P-1)+2).

    Los patrones uniformes reciben clases 0 .. P·(P-1)+1 en orden creciente
    de código; todos los no uniformes van a la última clase, P·(P-1)+2.
    """
    mascara = (1 << puntos) - 1
    tabla = np.empty(1 << puntos, dtype=np.int64)
    no_uniforme = puntos * (puntos - 1) + 2
    siguiente = 0
    for codigo in range(1 << puntos):
        rotado = ((codigo << 1) | (codigo >> (puntos - 1))) & mascara
        if (codigo ^ rotado).bit_count() <= 2:
            tabla[codigo] = siguiente
            siguiente += 1
        else:
            tabla[codigo] = no_uniforme
    assert siguiente == no_uniforme  # P·(P-1)+2 patrones uniformes
    return tabla


class ExtractorLBP(Extractor):
    nombre = "lbp"

    def __init__(self, radio: float = 1, puntos: int = 8, rejilla=(7, 7)):
        """
        Args:
            radio: radio R del círculo de vecinos, en píxeles (1 a 8).
            puntos: número P de vecinos (4 a 16).
            rejilla: [columnas, filas] de celdas, en el mismo orden que tam_cara.
        """
        radio = float(radio)
        puntos = int(puntos)
        rejilla = tuple(int(x) for x in rejilla)
        if not 1.0 <= radio <= 8.0:
            raise ValueError("radio debe estar entre 1 y 8")
        if not 4 <= puntos <= 16:
            raise ValueError("puntos debe estar entre 4 y 16")
        if len(rejilla) != 2 or not all(1 <= x <= 16 for x in rejilla):
            raise ValueError("rejilla debe ser [columnas, filas] con valores entre 1 y 16")

        self.radio = radio
        self.puntos = puntos
        self.rejilla = rejilla
        self.n_clases = puntos * (puntos - 1) + 3
        self.n_celdas = rejilla[0] * rejilla[1]
        self.dimension = self.n_clases * self.n_celdas
        self._tabla = tabla_uniforme(puntos)
        self._margen = math.ceil(radio)
        # Desplazamiento (dy, dx) de cada vecino. Solo se redondean los que caen
        # en un píxel exacto (0°, 90°...), para no interpolarlos por un error de
        # 1e-16; el resto se deja exacto para que los empates vecino == centro
        # no se rompan por redondeo.
        angulos = 2 * np.pi * np.arange(puntos) / puntos
        self._vecinos = [
            (_ajustar(-radio * math.sin(a)), _ajustar(radio * math.cos(a))) for a in angulos
        ]

    # ------------------------------------------------------------------ #
    # Extracción
    # ------------------------------------------------------------------ #
    def comprobar_tam_cara(self, tam: tuple[int, int]) -> None:
        ancho, alto = tam
        util_ancho, util_alto = ancho - 2 * self._margen, alto - 2 * self._margen
        if util_ancho < self.rejilla[0] or util_alto < self.rejilla[1]:
            raise ValueError(
                f"La cara de {ancho}x{alto} es demasiado pequeña para radio {self.radio:g} "
                f"y rejilla {self.rejilla[0]}x{self.rejilla[1]}"
            )

    def codigos(self, gris: np.ndarray) -> np.ndarray:
        """Mapa de códigos LBP (sin agrupar en clases) de una imagen en gris.

        Sale más pequeño que la entrada: se pierde un margen de ceil(R) píxeles
        por cada lado, donde el círculo de vecinos se sale de la imagen.
        """
        g = gris.astype(np.float64)
        m = self._margen
        alto, ancho = g.shape[0] - 2 * m, g.shape[1] - 2 * m
        centro = g[m:m + alto, m:m + ancho]
        codigos = np.zeros((alto, ancho), dtype=np.int64)
        for bit, (dy, dx) in enumerate(self._vecinos):
            vecino = self._muestrear(g, dy, dx, alto, ancho)
            # Pequeña tolerancia: en zonas planas la interpolación puede dar
            # un valor un pelín menor que el centro solo por redondeo.
            codigos |= (vecino >= centro - 1e-9).astype(np.int64) << bit
        return codigos

    def _muestrear(self, g: np.ndarray, dy: float, dx: float, alto: int, ancho: int) -> np.ndarray:
        """Imagen desplazada (dy, dx) con interpolación bilineal, recortada al centro."""
        m = self._margen
        y0, x0 = math.floor(dy), math.floor(dx)
        fy, fx = dy - y0, dx - x0
        resultado = np.zeros((alto, ancho), dtype=np.float64)
        # Solo se suman los términos con peso > 0: así nunca se lee fuera de la
        # imagen (y0+1 solo se usa si dy no es entero, y entonces y0+1 <= ceil(R)).
        for oy, peso_y in ((y0, 1 - fy), (y0 + 1, fy)):
            for ox, peso_x in ((x0, 1 - fx), (x0 + 1, fx)):
                peso = peso_y * peso_x
                if peso > 0:
                    resultado += peso * g[m + oy:m + oy + alto, m + ox:m + ox + ancho]
        return resultado

    def extraer(self, cara: np.ndarray) -> np.ndarray:
        if cara.ndim == 3:
            gris = cv2.cvtColor(cara, cv2.COLOR_BGR2GRAY)
        elif cara.ndim == 2:
            gris = cara
        else:
            raise ValueError(f"Se esperaba una imagen BGR o en gris (forma recibida {cara.shape})")
        self.comprobar_tam_cara((gris.shape[1], gris.shape[0]))

        clases = self._tabla[self.codigos(gris)]
        alto, ancho = clases.shape
        columnas, filas = self.rejilla
        # Índice de celda de cada píxel: filas y columnas repartidas lo más
        # igualadas posible (las celdas difieren como mucho en 1 px).
        fila = np.minimum(np.arange(alto) * filas // alto, filas - 1)
        columna = np.minimum(np.arange(ancho) * columnas // ancho, columnas - 1)
        celda = fila[:, None] * columnas + columna[None, :]

        conteos = np.bincount(
            (celda * self.n_clases + clases).ravel(), minlength=self.dimension
        ).reshape(self.n_celdas, self.n_clases).astype(np.float64)
        histogramas = conteos / conteos.sum(axis=1, keepdims=True) / self.n_celdas
        return histogramas.ravel().astype(np.float32)

    # ------------------------------------------------------------------ #
    # Comparación y validación
    # ------------------------------------------------------------------ #
    def _similitud(self, a: np.ndarray, b: np.ndarray) -> float:
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        suma = a + b
        validos = suma > 0
        chi2 = float(np.sum((a[validos] - b[validos]) ** 2 / suma[validos]))
        return 1.0 - chi2 / 2.0

    def _validar_valores(self, template: np.ndarray) -> None:
        if np.any(template < 0):
            raise TemplateInvalido("Un template LBP no puede tener valores negativos")
        sumas = template.reshape(self.n_celdas, self.n_clases).sum(axis=1, dtype=np.float64)
        esperado = 1.0 / self.n_celdas
        if np.any(np.abs(sumas - esperado) > TOLERANCIA_SUMA * esperado):
            raise TemplateInvalido(
                "El template LBP no está normalizado: cada celda debe sumar "
                f"1/{self.n_celdas}"
            )

    def __repr__(self) -> str:
        return (f"ExtractorLBP(radio={self.radio:g}, puntos={self.puntos}, "
                f"rejilla={list(self.rejilla)}, dimension={self.dimension})")
