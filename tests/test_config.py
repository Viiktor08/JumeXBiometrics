"""Tests de la carga y validación de config.yaml."""

import pytest

from biometria.config import RAIZ_REPO, cargar_config
from biometria.errores import ConfiguracionInvalida


def test_config_por_defecto_carga():
    cfg = cargar_config()
    assert cfg.preprocesado.tam_cara == (112, 112)
    assert cfg.detector.varias_caras in ("error", "mayor")
    # Las rutas relativas se resuelven respecto a la carpeta de config.yaml
    assert cfg.bd.ruta == (RAIZ_REPO / "data" / "bd.csv").resolve()


def test_valor_fuera_de_rango(tmp_path):
    ruta = tmp_path / "c.yaml"
    ruta.write_text("detector:\n  varias_caras: todas\n", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="varias_caras"):
        cargar_config(ruta)


def test_yaml_mal_formado(tmp_path):
    ruta = tmp_path / "c.yaml"
    ruta.write_text("detector: [sin cerrar\n", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida):
        cargar_config(ruta)


def test_tipo_incorrecto(tmp_path):
    ruta = tmp_path / "c.yaml"
    ruta.write_text("umbrales:\n  verificacion: alto\n", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="tipo incorrecto"):
        cargar_config(ruta)


def test_fichero_inexistente(tmp_path):
    with pytest.raises(ConfiguracionInvalida, match="No existe"):
        cargar_config(tmp_path / "no_existe.yaml")
