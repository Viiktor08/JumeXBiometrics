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
import shutil
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


# Identificación múltiple: como mucho se comprueban las caras más grandes
MAX_CARAS_IDENTIFICAR = 12


@dataclass
class CaraDeGrupo:
    """Una de las caras de una foto con varias personas."""

    deteccion: Deteccion
    procesada: CaraProcesada | None = None
    template: np.ndarray | None = None
    error: ErrorBiometrico | None = None  # por qué no se pudo comprobar (pequeña, borrosa...)


def analizar_todas(sistema: SistemaBiometrico, analisis: Analisis,
                   maximo: int = MAX_CARAS_IDENTIFICAR) -> list[CaraDeGrupo]:
    """Alinea y extrae el template de cada cara detectada (las `maximo` más grandes),
    ordenadas de izquierda a derecha para numerarlas como se leen en la foto."""
    caras = []
    for deteccion in analisis.detecciones[:maximo]:
        cara = CaraDeGrupo(deteccion)
        try:
            cara.procesada = sistema.detector.procesar_deteccion(analisis.imagen, deteccion)
            cara.template = sistema.extractor.extraer(cara.procesada.cara)
        except ErrorBiometrico as e:
            cara.error = e
        caras.append(cara)
    caras.sort(key=lambda c: c.deteccion.caja[0])
    return caras


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
    """Fotos de las personas inscritas, SOLO para mostrarlas en la web.

        <carpeta>/<persona>/<muestra>.jpg   una foto por muestra de la BD
        <carpeta>/<persona>/principal       clave de la muestra que hace de foto principal
        <carpeta>/<persona>.jpg             retrato de versiones anteriores (una por persona)

    <persona> es un hash del id y <muestra> la clave de la muestra en la BD
    (BaseDatos.clave_muestra): ningún nombre de fichero sale de lo que escribe
    el usuario, así que no hay forma de salirse de la carpeta.
    """

    LONGITUD_PERSONA = 20
    LONGITUD_MUESTRA = 16

    def __init__(self, carpeta: Path):
        self.carpeta = carpeta

    @staticmethod
    def clave(id_persona: str) -> str:
        return hashlib.sha256(validar_id(id_persona).encode("utf-8")).hexdigest()[:Galeria.LONGITUD_PERSONA]

    @staticmethod
    def _es_hex(texto: str, longitud: int) -> bool:
        return len(texto) == longitud and all(c in "0123456789abcdef" for c in texto)

    def carpeta_persona(self, id_persona: str) -> Path:
        return self.carpeta / self.clave(id_persona)

    def ruta_legado(self, id_persona: str) -> Path:
        return self.carpeta / f"{self.clave(id_persona)}.jpg"

    def ruta_muestra(self, id_persona: str, clave_muestra: str) -> Path:
        if not self._es_hex(clave_muestra, self.LONGITUD_MUESTRA):
            raise ValueError("clave de muestra no válida")
        return self.carpeta_persona(id_persona) / f"{clave_muestra}.jpg"

    # ------------------------------------------------------------------ #
    def _escribir(self, destino: Path, imagen: np.ndarray) -> bool:
        destino.parent.mkdir(parents=True, exist_ok=True)
        ok, datos = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            temporal = destino.with_suffix(".tmp")
            temporal.write_bytes(datos.tobytes())
            temporal.replace(destino)
        return bool(ok)

    def guardar_muestra(self, id_persona: str, clave_muestra: str, imagen: np.ndarray) -> bool:
        return self._escribir(self.ruta_muestra(id_persona, clave_muestra), imagen)

    def guardar_muestra_de_analisis(self, id_persona: str, clave_muestra: str, analisis: Analisis) -> bool:
        imagen = retrato_de_analisis(analisis)
        return imagen is not None and self.guardar_muestra(id_persona, clave_muestra, imagen)

    def tiene_foto(self, id_persona: str, clave_muestra: str) -> bool:
        return self.ruta_muestra(id_persona, clave_muestra).is_file()

    def principal(self, id_persona: str, claves: list[str]) -> str | None:
        """Clave de la muestra que hace de foto principal (la elegida, o la primera con foto)."""
        fichero = self.carpeta_persona(id_persona) / "principal"
        elegida = fichero.read_text(encoding="ascii").strip() if fichero.is_file() else None
        if elegida in claves and self.tiene_foto(id_persona, elegida):
            return elegida
        return next((c for c in claves if self.tiene_foto(id_persona, c)), None)

    def fijar_principal(self, id_persona: str, clave_muestra: str) -> None:
        self.ruta_muestra(id_persona, clave_muestra)  # valida la clave
        carpeta = self.carpeta_persona(id_persona)
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "principal").write_text(clave_muestra, encoding="ascii")

    # ------------------------------------------------------------------ #
    def eliminar(self, id_persona: str) -> None:
        shutil.rmtree(self.carpeta_persona(id_persona), ignore_errors=True)
        self.ruta_legado(id_persona).unlink(missing_ok=True)

    def eliminar_muestra(self, id_persona: str, clave_muestra: str) -> None:
        self.ruta_muestra(id_persona, clave_muestra).unlink(missing_ok=True)

    def renombrar(self, id_persona: str, nuevo: str, claves: dict[str, str]) -> None:
        """Mueve las fotos al nuevo id; `claves` traduce las claves de muestra que cambian."""
        origen, destino = self.carpeta_persona(id_persona), self.carpeta_persona(nuevo)
        if origen == destino:
            return
        if origen.is_dir():
            destino.mkdir(parents=True, exist_ok=True)
            for fichero in origen.iterdir():
                if fichero.suffix == ".jpg" and fichero.stem in claves:
                    fichero.replace(destino / f"{claves[fichero.stem]}.jpg")
            principal = origen / "principal"
            if principal.is_file():
                antigua = principal.read_text(encoding="ascii").strip()
                (destino / "principal").write_text(claves.get(antigua, antigua), encoding="ascii")
            shutil.rmtree(origen, ignore_errors=True)
        if self.ruta_legado(id_persona).is_file():
            self.ruta_legado(id_persona).replace(self.ruta_legado(nuevo))

    def cambiar_claves(self, id_persona: str, claves: dict[str, str]) -> None:
        """Renombra las fotos de una persona cuando cambian las claves de sus muestras
        (p. ej. al cifrar la BD: la clave es el hash del campo, que cambia)."""
        carpeta = self.carpeta_persona(id_persona)
        if not carpeta.is_dir():
            return
        temporales = []
        for vieja, nueva in claves.items():  # en dos pasos: una clave nueva podría ser otra vieja
            origen = carpeta / f"{vieja}.jpg"
            if vieja != nueva and origen.is_file():
                temporal = carpeta / f"{vieja}.jpg.mover"
                origen.replace(temporal)
                temporales.append((temporal, carpeta / f"{nueva}.jpg"))
        for temporal, destino in temporales:
            temporal.replace(destino)
        principal = carpeta / "principal"
        if principal.is_file():
            antigua = principal.read_text(encoding="ascii").strip()
            principal.write_text(claves.get(antigua, antigua), encoding="ascii")

    def migrar(self, muestras_por_id: dict[str, list[str]]) -> int:
        """Pasa los retratos antiguos (uno por persona) a foto de muestra cuando la
        persona tiene una sola muestra: entonces es seguro que es la suya."""
        migradas = 0
        for id_persona, claves in muestras_por_id.items():
            legado = self.ruta_legado(id_persona)
            if len(claves) == 1 and legado.is_file() and not self.tiene_foto(id_persona, claves[0]):
                destino = self.ruta_muestra(id_persona, claves[0])
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(legado, destino)
                migradas += 1
        return migradas

    # ------------------------------------------------------------------ #
    def ruta_por_url(self, partes: list[str]) -> Path | None:
        """Fichero a partir de la URL /api/caras/<persona>[/<muestra>].jpg (solo claves hex)."""
        if len(partes) == 1 and partes[0].endswith(".jpg"):
            persona = partes[0][:-4]
            ruta = self.carpeta / f"{persona}.jpg" if self._es_hex(persona, self.LONGITUD_PERSONA) else None
        elif len(partes) == 2 and partes[1].endswith(".jpg"):
            persona, muestra = partes[0], partes[1][:-4]
            ok = self._es_hex(persona, self.LONGITUD_PERSONA) and self._es_hex(muestra, self.LONGITUD_MUESTRA)
            ruta = self.carpeta / persona / f"{muestra}.jpg" if ok else None
        else:
            ruta = None
        return ruta if ruta is not None and ruta.is_file() else None
