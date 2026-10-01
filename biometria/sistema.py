"""SistemaBiometrico: los 9 métodos que pide el enunciado.

    método            entrada            pipeline
    ───────────────   ────────────────   ──────────────────────────────────────────
    inscribir         id, foto           detector ► calidad ► alineado ► extractor ► BD
    verificar         id, foto           detector ► calidad ► alineado ► extractor ► comparar
    identificar       foto               detector ► calidad ► alineado ► extractor ► comparar con todos
    *_sd              igual              la foto YA es una cara: redimensionar ► nitidez ► extractor ► ...
    *_t               template           validar template ► BD / comparar

Uso:
    from biometria.sistema import SistemaBiometrico
    sistema = SistemaBiometrico()
    sistema.inscribir("Ana López", "fotos/ana.jpg")
    sistema.verificar("Ana López", "fotos/ana_2.jpg")   # ResultadoVerificacion(aceptado=True, ...)
    sistema.identificar("fotos/quien.jpg")              # ResultadoIdentificacion(id='Ana López', ...)

La foto puede ser una ruta, los bytes del fichero o un array BGR de numpy.
Toda puntuación está en [0, 1] (1 = idénticos). Los errores controlados son
excepciones de biometria.errores, con un `codigo` estable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import cached_property

import numpy as np

from biometria.bd import BaseDatos, validar_id
from biometria.config import Config, cargar_config
from biometria.detector import Detector
from biometria.errores import IdNoInscrito
from biometria.extractores import crear_extractor
from biometria.imagen import OrigenImagen, cargar_imagen


@dataclass(frozen=True)
class ResultadoInscripcion:
    id: str
    muestras: int          # muestras de esa persona tras inscribir

    def a_dict(self) -> dict:
        return {"operacion": "inscribir", **asdict(self)}


@dataclass(frozen=True)
class ResultadoVerificacion:
    id: str
    aceptado: bool
    puntuacion: float      # similitud agregada frente a las muestras de `id`
    umbral: float

    def a_dict(self) -> dict:
        return {"operacion": "verificar", **asdict(self)}


@dataclass(frozen=True)
class ResultadoIdentificacion:
    id: str | None         # None = desconocido (nadie supera el umbral o BD vacía)
    puntuacion: float | None  # la del mejor candidato, lo supere o no
    umbral: float

    @property
    def identificado(self) -> bool:
        return self.id is not None

    def a_dict(self) -> dict:
        return {"operacion": "identificar", "identificado": self.identificado, **asdict(self)}


class SistemaBiometrico:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or cargar_config()
        self.extractor = crear_extractor(self.cfg)
        self.bd = BaseDatos(self.cfg, self.extractor)

    @cached_property
    def detector(self) -> Detector:
        # Se carga al primer uso: los métodos _t no necesitan el modelo YuNet.
        return Detector(self.cfg)

    # ------------------------------------------------------------------ #
    # Del dato de entrada al template
    # ------------------------------------------------------------------ #
    def extraer(self, foto: OrigenImagen) -> np.ndarray:
        """Foto -> template con el pipeline completo (detector incluido)."""
        imagen = cargar_imagen(foto, self.cfg.limites)
        return self.extractor.extraer(self.detector.procesar(imagen).cara)

    def extraer_sd(self, foto: OrigenImagen) -> np.ndarray:
        """Foto de una cara ya recortada -> template, sin detector ni alineado."""
        imagen = cargar_imagen(foto, self.cfg.limites)
        return self.extractor.extraer(self.detector.preparar_sin_detector(imagen).cara)

    # ------------------------------------------------------------------ #
    # Comparación
    # ------------------------------------------------------------------ #
    def _puntuar(self, template: np.ndarray, muestras: list[np.ndarray]) -> float:
        """Similitud frente a una persona: máximo o media sobre sus muestras."""
        puntuaciones = [self.extractor.comparar(template, m) for m in muestras]
        if self.cfg.bd.agregacion == "media":
            return float(np.mean(puntuaciones))
        return max(puntuaciones)

    def _inscribir(self, id_persona: str, template) -> ResultadoInscripcion:
        id_persona = validar_id(id_persona)
        return ResultadoInscripcion(id_persona, self.bd.inscribir(id_persona, template))

    def _verificar(self, id_persona: str, template) -> ResultadoVerificacion:
        id_persona = validar_id(id_persona)
        template = self.extractor.validar_template(template)
        puntuacion = self._puntuar(template, self.bd.templates_de(id_persona))
        umbral = self.cfg.umbrales.verificacion
        return ResultadoVerificacion(id_persona, puntuacion >= umbral, puntuacion, umbral)

    def _identificar(self, template) -> ResultadoIdentificacion:
        template = self.extractor.validar_template(template)
        umbral = self.cfg.umbrales.identificacion
        mejor_id, mejor = None, None
        # sorted: con empate gana siempre el mismo (el primero alfabéticamente)
        for id_persona, muestras in sorted(self.bd.por_id().items()):
            puntuacion = self._puntuar(template, muestras)
            if mejor is None or puntuacion > mejor:
                mejor_id, mejor = id_persona, puntuacion
        if mejor is None or mejor < umbral:
            return ResultadoIdentificacion(None, mejor, umbral)
        return ResultadoIdentificacion(mejor_id, mejor, umbral)

    def _comprobar_inscrito(self, id_persona: str) -> None:
        """Antes de procesar la foto: no gastar el detector en un id que no existe."""
        if validar_id(id_persona) not in self.bd.por_id():
            raise IdNoInscrito(f"'{validar_id(id_persona)}' no está inscrito")

    # ------------------------------------------------------------------ #
    # Los 9 métodos del enunciado
    # ------------------------------------------------------------------ #
    def inscribir(self, id_persona: str, foto: OrigenImagen) -> ResultadoInscripcion:
        validar_id(id_persona)
        return self._inscribir(id_persona, self.extraer(foto))

    def verificar(self, id_persona: str, foto: OrigenImagen) -> ResultadoVerificacion:
        self._comprobar_inscrito(id_persona)
        return self._verificar(id_persona, self.extraer(foto))

    def identificar(self, foto: OrigenImagen) -> ResultadoIdentificacion:
        return self._identificar(self.extraer(foto))

    def inscribir_sd(self, id_persona: str, foto: OrigenImagen) -> ResultadoInscripcion:
        validar_id(id_persona)
        return self._inscribir(id_persona, self.extraer_sd(foto))

    def verificar_sd(self, id_persona: str, foto: OrigenImagen) -> ResultadoVerificacion:
        self._comprobar_inscrito(id_persona)
        return self._verificar(id_persona, self.extraer_sd(foto))

    def identificar_sd(self, foto: OrigenImagen) -> ResultadoIdentificacion:
        return self._identificar(self.extraer_sd(foto))

    def inscribir_t(self, id_persona: str, template) -> ResultadoInscripcion:
        return self._inscribir(id_persona, template)

    def verificar_t(self, id_persona: str, template) -> ResultadoVerificacion:
        return self._verificar(id_persona, template)

    def identificar_t(self, template) -> ResultadoIdentificacion:
        return self._identificar(template)
