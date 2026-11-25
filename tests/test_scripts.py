"""Tests de los scripts auxiliares que no necesitan modelos."""

import sys

import pytest

from biometria.config import RAIZ_REPO
from biometria.errores import IdInvalido

sys.path.insert(0, str(RAIZ_REPO / "scripts"))
from preparar_dataset import nombre_de_fichero  # noqa: E402


@pytest.mark.parametrize("fichero, esperado", [
    ("christian_bale_2012.jpg", "Christian Bale"),
    ("mary-kate_olsen_2011.jpg", "Mary-Kate Olsen"),
    ("hwang_min-hyun_2021.png", "Hwang Min-Hyun"),
    ("elisabeth_II_1945.jpg", "Elisabeth II"),
    ("samuel_l_jackson_2012.jpg", "Samuel L Jackson"),
    ("jordi_hurtado_1985.jpeg", "Jordi Hurtado"),
    # Lo que va entre paréntesis y el año final no son parte del nombre
    ("james_phelps_(gemelo_weasley)_2011.jpg", "James Phelps"),
    ("aitana_etxebarria_(twin_melody)_2024.jpg", "Aitana Etxebarria"),
    ("adele.jpg", "Adele"),  # sin año también vale
])
def test_nombre_de_fichero(fichero, esperado):
    assert nombre_de_fichero(fichero) == esperado


def test_nombre_de_fichero_invalido():
    with pytest.raises(IdInvalido):
        nombre_de_fichero("_2012.jpg")  # sin nombre
