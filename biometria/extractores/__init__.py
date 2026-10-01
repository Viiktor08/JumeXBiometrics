"""Extractores de características intercambiables.

El extractor se elige en config.yaml (`extractor.nombre`) y sus parámetros se
pasan tal cual al constructor (`extractor.parametros`):

    from biometria.extractores import crear_extractor
    extractor = crear_extractor(cfg)
    template = extractor.extraer(cara_alineada)
    extractor.comparar(template, otro)   # -> puntuación en [0, 1]

Para añadir una técnica: subclase de Extractor (base.py) y una entrada en
EXTRACTORES.
"""

from __future__ import annotations

from biometria.config import Config
from biometria.errores import ConfiguracionInvalida
from biometria.extractores.base import Extractor
from biometria.extractores.lbp import ExtractorLBP
from biometria.extractores.sface import ExtractorSFace

EXTRACTORES: dict[str, type[Extractor]] = {
    ExtractorLBP.nombre: ExtractorLBP,
    ExtractorSFace.nombre: ExtractorSFace,
}


def crear_extractor(cfg: Config) -> Extractor:
    """Crea el extractor de la configuración y comprueba que admite tam_cara."""
    nombre = cfg.extractor.nombre
    if nombre not in EXTRACTORES:
        raise ConfiguracionInvalida(
            f"Extractor desconocido '{nombre}'. Disponibles: {', '.join(sorted(EXTRACTORES))}"
        )
    try:
        extractor = EXTRACTORES[nombre](**cfg.extractor.parametros)
        extractor.comprobar_tam_cara(cfg.preprocesado.tam_cara)
    except (TypeError, ValueError) as e:
        raise ConfiguracionInvalida(f"Parámetros no válidos para el extractor '{nombre}': {e}") from e
    return extractor


__all__ = ["EXTRACTORES", "Extractor", "ExtractorLBP", "ExtractorSFace", "crear_extractor"]
