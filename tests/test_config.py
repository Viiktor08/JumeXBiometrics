"""Tests de la carga y validación de config.yaml."""

import pytest

from biometria.config import RAIZ_REPO, cargar_config
from biometria.errores import ConfiguracionInvalida


def test_config_por_defecto_carga():
    cfg = cargar_config()
    assert cfg.preprocesado.tam_cara == (112, 112)
    assert cfg.detector.varias_caras in ("error", "mayor")
    # Las rutas relativas se resuelven respecto a la carpeta de config.yaml
    assert cfg.bd.ruta == (RAIZ_REPO / "data" / "lens" / "bd.csv").resolve()  # la BD de docker compose up


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


def test_config_por_defecto_usa_arcface():
    cfg = cargar_config()
    assert cfg.extractor.nombre == "arcface"
    assert cfg.umbrales.parecido <= min(cfg.umbrales.verificacion, cfg.umbrales.identificacion)
    # La demo web usa el mismo extractor y los mismos umbrales
    lens = cargar_config(RAIZ_REPO / "config_lens.yaml")
    assert lens.extractor.nombre == "arcface" and lens.umbrales == cfg.umbrales


def test_cli_docker_y_web_usan_la_misma_bd():
    # Sin --config, la CLI y el Docker (ENTRYPOINT python -m biometria) deben ver la
    # BD que crea `docker compose up` (Dataset/), no una BD vacía aparte.
    cfg = cargar_config()
    lens = cargar_config(RAIZ_REPO / "config_lens.yaml")
    assert cfg.bd.ruta == lens.bd.ruta and cfg.bd.ruta_clave == lens.bd.ruta_clave
    assert cfg.bd.ruta == RAIZ_REPO / "data" / "lens" / "bd.csv"
    assert cfg.detector == lens.detector and cfg.calidad == lens.calidad and cfg.defensa == lens.defensa


@pytest.mark.parametrize("parecido", [-0.1, 0.9])
def test_umbral_de_parecido_fuera_de_rango(tmp_path, parecido):
    ruta = tmp_path / "c.yaml"
    ruta.write_text(f"umbrales:\n  verificacion: 0.35\n  identificacion: 0.4\n  parecido: {parecido}\n",
                    encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="parecido"):
        cargar_config(ruta)
