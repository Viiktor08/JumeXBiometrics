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

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from functools import cached_property

import numpy as np

from biometria.bd import BaseDatos, validar_id
from biometria.config import Config, cargar_config
from biometria.detector import Detector
from biometria.errores import DemasiadosIntentos, IdNoInscrito, MuestraIncoherente
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
    # None: no se evaluó (operación con foto). True/False: el template venía (o no)
    # firmado por este sistema. Un template no firmado en _t puede ser fabricado.
    fiable: bool | None = None

    def a_dict(self) -> dict:
        return {"operacion": "verificar", **asdict(self)}


@dataclass(frozen=True)
class ResultadoIdentificacion:
    id: str | None         # None = desconocido (nadie supera el umbral o BD vacía)
    puntuacion: float | None  # la del mejor candidato, lo supere o no
    umbral: float
    fiable: bool | None = None  # como en ResultadoVerificacion

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
        # Marcas de tiempo de los últimos intentos _t por id, para el límite anti hill-climbing
        self._intentos: dict[str, deque[float]] = {}
        self._intentos_lock = threading.Lock()

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
    # Defensas de los métodos _t (ver THREAT_MODEL.md, pasos 2 y 3)
    # ------------------------------------------------------------------ #
    def firmar(self, template) -> str:
        """Firma HMAC de un template generado por este sistema (para los ficheros .sig)."""
        return self.bd.firmar_template(self.extractor.validar_template(template))

    def _fiable(self, template, firma: str | None) -> bool | None:
        """¿El template de un método _t viene firmado por nosotros? None si no se pasó firma."""
        if firma is None:
            return None
        return self.bd.template_firmado(self.extractor.validar_template(template), firma)

    def _redondear(self, puntuacion: float | None) -> float | None:
        """Recorta decimales de la puntuación que sale por _t, para no guiar un hill-climbing."""
        n = self.cfg.defensa.decimales_puntuacion_t
        return puntuacion if puntuacion is None or n <= 0 else round(puntuacion, n)

    def _contar_intento(self, clave: str) -> None:
        """Cuenta un intento _t para `clave` y lanza DemasiadosIntentos si se pasa del límite."""
        maximo = self.cfg.defensa.max_intentos_t
        if maximo <= 0:
            return
        ventana = self.cfg.defensa.ventana_intentos_s
        ahora = time.monotonic()
        with self._intentos_lock:
            marcas = self._intentos.setdefault(clave, deque())
            while marcas and ahora - marcas[0] > ventana:
                marcas.popleft()
            if len(marcas) >= maximo:
                espera = ventana - (ahora - marcas[0])
                raise DemasiadosIntentos(
                    f"Demasiadas comparaciones por template ({maximo} en {ventana:.0f}s). "
                    f"Espera {espera:.0f}s. (Protección contra el ajuste automático de templates.)"
                )
            marcas.append(ahora)

    # ------------------------------------------------------------------ #
    # Comparación
    # ------------------------------------------------------------------ #
    def _puntuar(self, template: np.ndarray, muestras: list[np.ndarray]) -> float:
        """Similitud frente a una persona: máximo o media sobre sus muestras."""
        puntuaciones = [self.extractor.comparar(template, m) for m in muestras]
        if self.cfg.bd.agregacion == "media":
            return float(np.mean(puntuaciones))
        return max(puntuaciones)

    def _comprobar_coherencia(self, id_persona: str, template) -> None:
        """Defensa contra el envenenamiento de la inscripción: una muestra nueva de
        una persona que YA existe tiene que parecerse a las que ya tiene. Así no se
        puede añadir la cara de un atacante como muestra de la víctima.

        Solo se aplica al AÑADIR a alguien existente: la primera muestra (persona
        nueva) no tiene con qué compararse, y crear a alguien es responsabilidad de
        quien inscribe, no de este sistema. Ver THREAT_MODEL.md."""
        umbral = self.cfg.defensa.coherencia_muestra
        if umbral <= 0.0:
            return
        anteriores = self.bd.por_id().get(validar_id(id_persona))
        if not anteriores:
            return  # persona nueva: nada con que comparar
        template = self.extractor.validar_template(template)
        puntuacion = self._puntuar(template, anteriores)
        if puntuacion < umbral:
            raise MuestraIncoherente(
                f"La foto no se parece lo suficiente a las muestras de '{validar_id(id_persona)}' "
                f"(similitud {puntuacion:.2f}, mínimo {umbral:.2f}). Si de verdad es esa persona, "
                "usa una foto más parecida a las que ya tiene; si es otra persona, inscríbela con su nombre."
            )

    def _inscribir(self, id_persona: str, template) -> ResultadoInscripcion:
        id_persona = validar_id(id_persona)
        self._comprobar_coherencia(id_persona, template)
        return ResultadoInscripcion(id_persona, self.bd.inscribir(id_persona, template))

    def _verificar(self, id_persona: str, template, fiable: bool | None = None,
                   redondear: bool = False) -> ResultadoVerificacion:
        id_persona = validar_id(id_persona)
        template = self.extractor.validar_template(template)
        puntuacion = self._puntuar(template, self.bd.templates_de(id_persona))
        umbral = self.cfg.umbrales.verificacion
        aceptado = puntuacion >= umbral  # la decisión usa la puntuación exacta
        if redondear:
            puntuacion = self._redondear(puntuacion)
        return ResultadoVerificacion(id_persona, aceptado, puntuacion, umbral, fiable)

    def ranking(self, template, n: int | None = None) -> list[tuple[str, float]]:
        """Personas de la BD ordenadas de más a menos parecida: [(id, puntuación), ...].

        Con empate, primero el id alfabéticamente menor (resultado determinista).
        """
        template = self.extractor.validar_template(template)
        puntuaciones = [(id_persona, self._puntuar(template, muestras))
                        for id_persona, muestras in self.bd.por_id().items()]
        puntuaciones.sort(key=lambda par: (-par[1], par[0]))
        return puntuaciones[:n] if n is not None else puntuaciones

    def _identificar(self, template, fiable: bool | None = None,
                     redondear: bool = False) -> ResultadoIdentificacion:
        umbral = self.cfg.umbrales.identificacion
        mejores = self.ranking(template, 1)
        if not mejores:
            return ResultadoIdentificacion(None, None, umbral, fiable)
        mejor_id, mejor = mejores[0]
        identificado = mejor >= umbral  # la decisión usa la puntuación exacta
        puntuacion = self._redondear(mejor) if redondear else mejor
        return ResultadoIdentificacion(mejor_id if identificado else None, puntuacion, umbral, fiable)

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

    def inscribir_t(self, id_persona: str, template, firma: str | None = None) -> ResultadoInscripcion:
        # Firma opcional: inscribir no devuelve puntuación, pero sí registra si el
        # template es nuestro (quien lo use puede rechazar inscribir templates ajenos).
        self._fiable(template, firma)
        return self._inscribir(id_persona, template)

    def verificar_t(self, id_persona: str, template, firma: str | None = None,
                    _contar: bool = True) -> ResultadoVerificacion:
        # _contar=False cuando el template viene de una foto que el sistema acaba de
        # procesar (la web): ahí no hay hill-climbing, no gasta el cupo de intentos.
        if _contar:
            self._contar_intento(validar_id(id_persona))  # límite anti hill-climbing por id
        fiable = self._fiable(template, firma)
        return self._verificar(id_persona, template, fiable=fiable, redondear=True)

    def identificar_t(self, template, firma: str | None = None,
                      _contar: bool = True) -> ResultadoIdentificacion:
        if _contar:
            self._contar_intento("__identificar__")  # el 1:N no tiene id: un único cupo
        fiable = self._fiable(template, firma)
        return self._identificar(template, fiable=fiable, redondear=True)
