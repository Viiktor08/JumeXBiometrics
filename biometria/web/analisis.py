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
from biometria.detector import PLANTILLA_LANDMARKS_112, CaraProcesada, Deteccion, iou
from biometria.errores import CaraPequena, ErrorBiometrico
from biometria.imagen import cargar_imagen
from biometria.sistema import SistemaBiometrico

LADO_RETRATO = 256
# Región marcada a mano: se amplía este margen por cada lado antes de volver a
# buscar la cara dentro (el recuadro del usuario suele quedar justo).
MARGEN_REGION = 0.25
LADO_MINIMO_REGION = 24
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
    # "automatica": la eligió el detector; "manual": se buscó dentro del recuadro
    # del usuario; "manual_sin_puntos": tampoco ahí, se usa el recuadro sin alinear.
    seleccion: str = "automatica"
    region: tuple[int, int, int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.template is not None


def _recortar_region(imagen: np.ndarray, region, margen: float) -> tuple[np.ndarray, int, int]:
    """Recorte de la región ampliada con `margen`, recortada a los bordes. Devuelve (recorte, x0, y0)."""
    alto, ancho = imagen.shape[:2]
    x, y, w, h = region
    mx, my = int(w * margen), int(h * margen)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(ancho, x + w + mx), min(alto, y + h + my)
    return imagen[y0:y1, x0:x1], x0, y0


def validar_region(region, imagen: np.ndarray) -> tuple[int, int, int, int]:
    """Recorta la región (x, y, ancho, alto) a la imagen y comprueba que tiene tamaño útil."""
    alto, ancho = imagen.shape[:2]
    x, y, w, h = (int(round(v)) for v in region)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(ancho, x + w), min(alto, y + h)
    if x1 - x0 < LADO_MINIMO_REGION or y1 - y0 < LADO_MINIMO_REGION:
        raise CaraPequena(f"El recuadro marcado es demasiado pequeño (mínimo {LADO_MINIMO_REGION} px)")
    return x0, y0, x1 - x0, y1 - y0


def _cara_en_region(sistema: SistemaBiometrico, imagen: np.ndarray, region) -> Deteccion | None:
    """Busca la cara dentro del recuadro del usuario (con margen) y la devuelve en
    coordenadas de la imagen completa; la que más se solapa con el recuadro."""
    recorte, x0, y0 = _recortar_region(imagen, region, MARGEN_REGION)
    # Umbral más bajo que el automático: el usuario ya ha dicho dónde está la cara.
    umbral = min(sistema.cfg.detector.umbral_confianza, 0.5)
    candidatas = sistema.detector.detectar(recorte, umbral_confianza=umbral)
    if not candidatas:
        return None
    desplazadas = [
        Deteccion(caja=(d.caja[0] + x0, d.caja[1] + y0, d.caja[2], d.caja[3]),
                  landmarks=d.landmarks + np.array([x0, y0], dtype=np.float32),
                  confianza=d.confianza)
        for d in candidatas
    ]
    return max(desplazadas, key=lambda d: iou(d.caja, region))


def analizar(sistema: SistemaBiometrico, foto, region=None) -> Analisis:
    """Carga ► detecta ► elige cara ► calidad y alineado ► extrae, cronometrando cada paso.

    Con `region` (x, y, ancho, alto en píxeles de la imagen) la cara la marca el
    usuario: se busca dentro de ese recuadro y, si no aparece, se usa el
    recuadro tal cual, sin alinear (como los métodos _sd).
    """
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
        if region is None:
            analisis.detecciones = sistema.detector.detectar(analisis.imagen)
            marcar("deteccion")
            elegida = sistema.detector.elegir_cara(analisis.detecciones)
            analisis.procesada = sistema.detector.procesar_deteccion(analisis.imagen, elegida)
        else:
            analisis.region = validar_region(region, analisis.imagen)
            elegida = _cara_en_region(sistema, analisis.imagen, analisis.region)
            marcar("deteccion")
            if elegida is not None:
                analisis.seleccion = "manual"
                analisis.detecciones = [elegida]
                analisis.procesada = sistema.detector.procesar_deteccion(analisis.imagen, elegida)
            else:
                analisis.seleccion = "manual_sin_puntos"
                x, y, w, h = analisis.region
                analisis.procesada = sistema.detector.preparar_sin_detector(
                    analisis.imagen[y:y + h, x:x + w])
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


def retrato_de_analisis(analisis: Analisis) -> np.ndarray | None:
    """Retrato para mostrar: alineado si hay landmarks; si la cara se marcó a mano
    sin puntos, el recuadro hecho cuadrado."""
    if analisis.imagen is None or analisis.procesada is None:
        return None
    if analisis.procesada.deteccion is not None:
        return retrato(analisis.imagen, analisis.procesada.deteccion.landmarks)
    if analisis.region is None:
        return None
    x, y, w, h = analisis.region
    lado = max(w, h)
    cx, cy = x + w // 2, y + h // 2
    alto, ancho = analisis.imagen.shape[:2]
    x0, y0 = max(0, cx - lado // 2), max(0, cy - lado // 2)
    recorte = analisis.imagen[y0:min(alto, y0 + lado), x0:min(ancho, x0 + lado)]
    return cv2.resize(recorte, (LADO_RETRATO, LADO_RETRATO), interpolation=cv2.INTER_AREA)


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
        imagen = retrato_de_analisis(analisis)
        return imagen is not None and self.guardar(id_persona, imagen)

    def eliminar(self, id_persona: str) -> None:
        self.ruta(id_persona).unlink(missing_ok=True)

    def ruta_por_clave(self, clave: str) -> Path | None:
        """Ruta del retrato a partir de la clave de la URL (solo hex de 20)."""
        if len(clave) != 20 or any(c not in "0123456789abcdef" for c in clave):
            return None
        ruta = self.carpeta / f"{clave}.jpg"
        return ruta if ruta.is_file() else None
