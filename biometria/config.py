"""Carga y validación de la configuración (config.yaml).

Uso típico:
    from biometria.config import cargar_config
    cfg = cargar_config()                # config.yaml del repo o BIOMETRIA_CONFIG
    cfg.umbrales.verificacion            # -> 0.5

La configuración se valida al cargarla: un valor fuera de rango da un
ConfiguracionInvalida claro en vez de un fallo raro más adelante.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from biometria import LIMITE_DURO_PIXELES
from biometria.errores import ConfiguracionInvalida

# Raíz del repositorio (carpeta que contiene el paquete `biometria`).
RAIZ_REPO = Path(__file__).resolve().parent.parent
RUTA_CONFIG_POR_DEFECTO = RAIZ_REPO / "config.yaml"


@dataclass(frozen=True)
class ConfigDetector:
    modelo: Path
    umbral_confianza: float = 0.8
    umbral_nms: float = 0.3
    varias_caras: str = "error"  # "error" | "mayor"


@dataclass(frozen=True)
class ConfigPreprocesado:
    tam_cara: tuple[int, int] = (112, 112)  # (ancho, alto)


@dataclass(frozen=True)
class ConfigCalidad:
    tam_min_cara: int = 60
    umbral_borrosidad: float = 50.0


@dataclass(frozen=True)
class ConfigExtractor:
    nombre: str = "arcface"
    parametros: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigBD:
    ruta: Path
    ruta_clave: Path
    cifrar_templates: bool = False
    agregacion: str = "max"  # "max" | "media"
    # Sello anti-rollback: hash firmado del último CSV escrito, guardado junto a la
    # clave (no junto a la BD). Detecta que alguien vuelva a una copia antigua válida.
    anti_rollback: bool = False


@dataclass(frozen=True)
class ConfigUmbrales:
    verificacion: float = 0.5
    identificacion: float = 0.5
    # Solo para mostrar: por debajo, un candidato "no se parece" (la web no lo lista
    # entre los más parecidos). No interviene en ninguna decisión del sistema.
    parecido: float = 0.0


@dataclass(frozen=True)
class ConfigLimites:
    max_bytes_imagen: int = 10 * 1024 * 1024
    max_pixeles: int = 25_000_000
    max_muestras_por_id: int = 10


@dataclass(frozen=True)
class ConfigDefensa:
    """Contramedidas frente a ataques (ver THREAT_MODEL.md). Todas se pueden
    desactivar para medir el sistema sin ellas o para no romper el enunciado."""

    # Paso 1: al añadir una muestra a una persona que ya existe, su similitud con
    # la PRIMERA muestra (la inscripción original) debe llegar a este umbral
    # (0 = desactivado). Corta el envenenamiento de la inscripción (añadir otra cara
    # con el nombre de la víctima) y la deriva paso a paso con morphs.
    coherencia_muestra: float = 0.0
    # Paso 1 (ancla): al verificar o identificar, las muestras extra de una persona
    # solo cuentan si la foto se parece al menos esto a su PRIMERA muestra; si no,
    # la puntuación es la de la primera. Así una muestra envenenada (un morph que se
    # parece a víctima y atacante) no sirve para colar al atacante. 0 = desactivado.
    ancla_muestra: float = 0.0
    # Paso 3 (hill-climbing): nº de decimales a los que se redondea la puntuación que
    # devuelven los métodos _t (quien no ve la foto). Menos decimales = menos pistas
    # para ir ajustando un template hasta colarlo. 0 = sin redondeo.
    decimales_puntuacion_t: int = 0
    # Paso 3: nº máximo de llamadas a verificar_t/identificar_t por id antes de
    # frenarlas (0 = sin límite). Pensado contra el barrido automático de templates.
    max_intentos_t: int = 0
    # Ventana (segundos) en la que cuenta ese límite de intentos.
    ventana_intentos_s: float = 60.0
    # Paso 2 (estricto): los métodos _t RECHAZAN los templates sin una firma válida
    # de este sistema (los que no salen de `extraer`). False = solo avisan (`fiable`).
    exigir_firma_t: bool = False
    # Paso 4 (gemelos/dobles): en identificar, el 1.º candidato debe sacar al 2.º al
    # menos este margen; si no, el resultado es "desconocido (ambiguo)". 0 = desactivado.
    margen_identificacion: float = 0.0
    # Paso 5: los métodos _sd comprueban que la imagen contiene una cara (con YuNet),
    # aunque no la usen para alinear. Evita meter ruido o imágenes que no son caras.
    cara_en_sd: bool = False


@dataclass(frozen=True)
class Config:
    detector: ConfigDetector
    preprocesado: ConfigPreprocesado
    calidad: ConfigCalidad
    extractor: ConfigExtractor
    bd: ConfigBD
    umbrales: ConfigUmbrales
    limites: ConfigLimites
    defensa: ConfigDefensa


def _seccion(datos: dict, nombre: str) -> dict:
    """Devuelve una sección del YAML (o {} si falta) comprobando que es un dict."""
    valor = datos.get(nombre) or {}
    if not isinstance(valor, dict):
        raise ConfiguracionInvalida(f"La sección '{nombre}' debe ser un diccionario")
    return valor


def _ruta(valor: str, base: Path) -> Path:
    """Resuelve una ruta relativa respecto a la carpeta del fichero de config."""
    ruta = Path(valor).expanduser()  # "~/.jumex/clave.key" -> carpeta del usuario
    return ruta if ruta.is_absolute() else (base / ruta).resolve()


def _construir(datos: dict, base: Path) -> Config:
    det = _seccion(datos, "detector")
    pre = _seccion(datos, "preprocesado")
    cal = _seccion(datos, "calidad")
    ext = _seccion(datos, "extractor")
    bd = _seccion(datos, "bd")
    umb = _seccion(datos, "umbrales")
    lim = _seccion(datos, "limites")
    defe = _seccion(datos, "defensa")

    try:
        return Config(
            detector=ConfigDetector(
                modelo=_ruta(det.get("modelo", "models/face_detection_yunet_2023mar.onnx"), base),
                umbral_confianza=float(det.get("umbral_confianza", 0.8)),
                umbral_nms=float(det.get("umbral_nms", 0.3)),
                varias_caras=str(det.get("varias_caras", "error")),
            ),
            preprocesado=ConfigPreprocesado(
                tam_cara=tuple(int(x) for x in pre.get("tam_cara", (112, 112))),
            ),
            calidad=ConfigCalidad(
                tam_min_cara=int(cal.get("tam_min_cara", 60)),
                umbral_borrosidad=float(cal.get("umbral_borrosidad", 50.0)),
            ),
            extractor=ConfigExtractor(
                nombre=str(ext.get("nombre", "arcface")),
                parametros=dict(ext.get("parametros") or {}),
            ),
            bd=ConfigBD(
                ruta=_ruta(bd.get("ruta", "data/bd.csv"), base),
                ruta_clave=_ruta(bd.get("ruta_clave", "data/clave_hmac.key"), base),
                cifrar_templates=bool(bd.get("cifrar_templates", False)),
                agregacion=str(bd.get("agregacion", "max")),
                anti_rollback=bool(bd.get("anti_rollback", False)),
            ),
            umbrales=ConfigUmbrales(
                verificacion=float(umb.get("verificacion", 0.5)),
                identificacion=float(umb.get("identificacion", 0.5)),
                parecido=float(umb.get("parecido", 0.0)),
            ),
            limites=ConfigLimites(
                max_bytes_imagen=int(lim.get("max_bytes_imagen", 10 * 1024 * 1024)),
                max_pixeles=int(lim.get("max_pixeles", 25_000_000)),
                max_muestras_por_id=int(lim.get("max_muestras_por_id", 10)),
            ),
            defensa=ConfigDefensa(
                coherencia_muestra=float(defe.get("coherencia_muestra", 0.0)),
                ancla_muestra=float(defe.get("ancla_muestra", 0.0)),
                decimales_puntuacion_t=int(defe.get("decimales_puntuacion_t", 0)),
                max_intentos_t=int(defe.get("max_intentos_t", 0)),
                ventana_intentos_s=float(defe.get("ventana_intentos_s", 60.0)),
                exigir_firma_t=bool(defe.get("exigir_firma_t", False)),
                margen_identificacion=float(defe.get("margen_identificacion", 0.0)),
                cara_en_sd=bool(defe.get("cara_en_sd", False)),
            ),
        )
    except (TypeError, ValueError) as e:
        raise ConfiguracionInvalida(f"Valor de configuración con tipo incorrecto: {e}") from e


def _validar(cfg: Config) -> None:
    """Comprueba rangos y opciones permitidas."""
    errores = []
    if cfg.detector.varias_caras not in ("error", "mayor"):
        errores.append("detector.varias_caras debe ser 'error' o 'mayor'")
    if not 0.0 < cfg.detector.umbral_confianza <= 1.0:
        errores.append("detector.umbral_confianza debe estar en (0, 1]")
    if not 0.0 < cfg.detector.umbral_nms <= 1.0:
        errores.append("detector.umbral_nms debe estar en (0, 1]")
    if len(cfg.preprocesado.tam_cara) != 2 or min(cfg.preprocesado.tam_cara) < 16:
        errores.append("preprocesado.tam_cara debe ser [ancho, alto] con lados >= 16")
    if cfg.calidad.tam_min_cara < 0 or cfg.calidad.umbral_borrosidad < 0:
        errores.append("los parámetros de calidad no pueden ser negativos")
    if cfg.bd.agregacion not in ("max", "media"):
        errores.append("bd.agregacion debe ser 'max' o 'media'")
    if not (0.0 <= cfg.umbrales.verificacion <= 1.0 and 0.0 <= cfg.umbrales.identificacion <= 1.0):
        errores.append("los umbrales deben estar en [0, 1] (las puntuaciones están en ese rango)")
    if not 0.0 <= cfg.umbrales.parecido <= min(cfg.umbrales.verificacion, cfg.umbrales.identificacion):
        errores.append("umbrales.parecido debe estar entre 0 y el menor de los otros dos umbrales")
    if cfg.limites.max_bytes_imagen <= 0 or cfg.limites.max_pixeles <= 0:
        errores.append("los límites de imagen deben ser positivos")
    if cfg.limites.max_pixeles > LIMITE_DURO_PIXELES:
        errores.append(f"limites.max_pixeles no puede superar el límite duro ({LIMITE_DURO_PIXELES})")
    if cfg.limites.max_muestras_por_id < 1:
        errores.append("limites.max_muestras_por_id debe ser >= 1")
    if not 0.0 <= cfg.defensa.coherencia_muestra <= 1.0:
        errores.append("defensa.coherencia_muestra debe estar en [0, 1]")
    if not 0.0 <= cfg.defensa.ancla_muestra <= 1.0:
        errores.append("defensa.ancla_muestra debe estar en [0, 1]")
    if not 0 <= cfg.defensa.decimales_puntuacion_t <= 10:
        errores.append("defensa.decimales_puntuacion_t debe estar entre 0 y 10")
    if cfg.defensa.max_intentos_t < 0:
        errores.append("defensa.max_intentos_t no puede ser negativo")
    if cfg.defensa.ventana_intentos_s <= 0:
        errores.append("defensa.ventana_intentos_s debe ser positiva")
    if not 0.0 <= cfg.defensa.margen_identificacion <= 1.0:
        errores.append("defensa.margen_identificacion debe estar en [0, 1]")
    if errores:
        raise ConfiguracionInvalida("; ".join(errores))


def cargar_config(ruta: str | Path | None = None) -> Config:
    """Carga la configuración.

    Orden de prioridad para elegir el fichero:
      1. argumento `ruta`
      2. variable de entorno BIOMETRIA_CONFIG
      3. config.yaml en la raíz del repositorio
    """
    ruta = Path(ruta or os.environ.get("BIOMETRIA_CONFIG") or RUTA_CONFIG_POR_DEFECTO)
    if not ruta.is_file():
        raise ConfiguracionInvalida(f"No existe el fichero de configuración: {ruta}")

    try:
        # safe_load: nunca construye objetos Python arbitrarios desde el YAML.
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfiguracionInvalida(f"config.yaml mal formado: {e}") from e
    if not isinstance(datos, dict):
        raise ConfiguracionInvalida("La raíz de config.yaml debe ser un diccionario")

    cfg = _construir(datos, base=ruta.resolve().parent)
    _validar(cfg)
    return cfg
