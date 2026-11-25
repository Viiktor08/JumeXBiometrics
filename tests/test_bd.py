"""Tests de la base de datos CSV: formato, integridad (HMAC), cifrado e ids."""

import stat
import sys

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE, BaseDatos, clave_busqueda, validar_id
from biometria.errores import (
    BDCorrupta,
    ConfiguracionInvalida,
    IdInvalido,
    IdNoInscrito,
    IntegridadBD,
    LimiteMuestras,
    MuestraNoEncontrada,
    TemplateInvalido,
)
from biometria.extractores import ExtractorLBP
from tests.conftest import cfg_con

CLAVE = "k" * 40


@pytest.fixture
def extractor():
    return ExtractorLBP()


def template_aleatorio(extractor, semilla):
    """Template LBP válido (cada celda normalizada) sin necesidad de fotos."""
    rng = np.random.default_rng(semilla)
    conteos = rng.random((extractor.n_celdas, extractor.n_clases))
    return (conteos / conteos.sum(axis=1, keepdims=True) / extractor.n_celdas).ravel().astype(np.float32)


@pytest.fixture
def cfg_tmp(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    # Base de estos tests: en claro y sin sello (cada defensa tiene sus propios tests)
    return cfg_con(cfg, bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "clave.key",
                            "cifrar_templates": False, "anti_rollback": False})


@pytest.fixture
def bd(cfg_tmp, extractor):
    return BaseDatos(cfg_tmp, extractor)


def bd_con_tres_filas(bd, extractor):
    bd.inscribir("Ana", template_aleatorio(extractor, 1))
    bd.inscribir("Ana", template_aleatorio(extractor, 2))
    bd.inscribir("Luis", template_aleatorio(extractor, 3))
    return bd.ruta.read_text(encoding="utf-8").splitlines()


def escribir_lineas(bd, lineas):
    bd.ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


# --- Uso normal ---------------------------------------------------------------------

def test_sin_fichero_es_una_bd_vacia(bd):
    assert bd.muestras() == []
    assert bd.ids() == []
    assert not bd.cfg.bd.ruta_clave.exists()  # leer no crea la clave


def test_inscribir_y_leer_conserva_el_template_exacto(bd, extractor):
    t = template_aleatorio(extractor, 0)
    assert bd.inscribir("Ana", t) == 1
    [muestra] = bd.muestras()
    assert muestra.id == "Ana"
    assert np.array_equal(muestra.template, t)


def test_varias_muestras_por_persona(bd, extractor):
    bd_con_tres_filas(bd, extractor)
    assert bd.ids() == ["Ana", "Luis"]
    assert len(bd.templates_de("Ana")) == 2
    assert len(bd.templates_de("Luis")) == 1
    assert bd.inscribir("Luis", template_aleatorio(extractor, 9)) == 2


def test_formato_del_fichero(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    assert lineas[0] == "id,template,hmac"
    assert len(lineas) == 4
    id_persona, campo, firma = lineas[1].split(",")
    assert id_persona == "Ana"
    assert campo.startswith("f32:")
    assert len(firma) == 64  # SHA-256 en hexadecimal


def test_id_no_inscrito(bd, extractor):
    bd.inscribir("Ana", template_aleatorio(extractor, 0))
    with pytest.raises(IdNoInscrito):
        bd.templates_de("Pepe")


def test_limite_de_muestras(cfg_tmp, extractor):
    bd = BaseDatos(cfg_con(cfg_tmp, limites={"max_muestras_por_id": 2}), extractor)
    bd.inscribir("Ana", template_aleatorio(extractor, 0))
    bd.inscribir("Ana", template_aleatorio(extractor, 1))
    with pytest.raises(LimiteMuestras):
        bd.inscribir("Ana", template_aleatorio(extractor, 2))
    bd.inscribir("Luis", template_aleatorio(extractor, 3))  # el límite es por persona


def test_no_guarda_templates_invalidos(bd, extractor):
    with pytest.raises(TemplateInvalido):
        bd.inscribir("Ana", np.ones(extractor.dimension))
    assert not bd.ruta.exists()


def test_escritura_atomica_no_deja_temporales(bd, extractor):
    bd_con_tres_filas(bd, extractor)
    # (.bd.csv.lock es el fichero del bloqueo entre procesos, vacío y permanente)
    assert sorted(p.name for p in bd.ruta.parent.iterdir()) == [".bd.csv.lock", "bd.csv", "clave.key"]


# --- Clave ----------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_la_clave_se_crea_al_inscribir_solo_para_el_dueño(bd, extractor):
    bd.inscribir("Ana", template_aleatorio(extractor, 0))
    clave = bd.cfg.bd.ruta_clave
    assert len(clave.read_text().strip()) == 64
    assert stat.S_IMODE(clave.stat().st_mode) == 0o600


def test_la_variable_de_entorno_tiene_prioridad(cfg_tmp, extractor, monkeypatch):
    monkeypatch.setenv(VARIABLE_CLAVE, CLAVE)
    BaseDatos(cfg_tmp, extractor).inscribir("Ana", template_aleatorio(extractor, 0))
    assert not cfg_tmp.bd.ruta_clave.exists()
    assert BaseDatos(cfg_tmp, extractor).ids() == ["Ana"]


def test_clave_corta(cfg_tmp, extractor, monkeypatch):
    monkeypatch.setenv(VARIABLE_CLAVE, "corta")
    with pytest.raises(ConfiguracionInvalida, match="corta"):
        BaseDatos(cfg_tmp, extractor).inscribir("Ana", template_aleatorio(extractor, 0))


def test_bd_existente_sin_clave(bd, extractor):
    bd_con_tres_filas(bd, extractor)
    bd.cfg.bd.ruta_clave.unlink()
    with pytest.raises(ConfiguracionInvalida, match="Falta la clave"):
        BaseDatos(bd.cfg, extractor).muestras()


def test_clave_incorrecta(cfg_tmp, extractor, monkeypatch):
    monkeypatch.setenv(VARIABLE_CLAVE, CLAVE)
    BaseDatos(cfg_tmp, extractor).inscribir("Ana", template_aleatorio(extractor, 0))
    monkeypatch.setenv(VARIABLE_CLAVE, "x" * 40)
    with pytest.raises(IntegridadBD):
        BaseDatos(cfg_tmp, extractor).muestras()


# --- Ataques a la integridad (el atacante puede escribir en el CSV) ----------------------

def test_detecta_template_modificado(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    id_persona, campo, firma = lineas[1].split(",")
    otro = "A" if campo[10] != "A" else "B"
    lineas[1] = ",".join([id_persona, campo[:10] + otro + campo[11:], firma])
    escribir_lineas(bd, lineas)
    with pytest.raises(IntegridadBD, match="Fila 2"):
        bd.muestras()


def test_detecta_template_de_otra_persona(bd, extractor):
    # El atacante copia el template de Luis en la fila de Ana (dejando la firma de Ana).
    lineas = bd_con_tres_filas(bd, extractor)
    campo_luis = lineas[3].split(",")[1]
    id_ana, _, firma_ana = lineas[1].split(",")
    lineas[1] = ",".join([id_ana, campo_luis, firma_ana])
    escribir_lineas(bd, lineas)
    with pytest.raises(IntegridadBD):
        bd.muestras()


def test_detecta_id_cambiado(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    lineas[3] = lineas[3].replace("Luis", "Ana", 1)
    escribir_lineas(bd, lineas)
    with pytest.raises(IntegridadBD):
        bd.muestras()


@pytest.mark.parametrize("ataque", ["borrar_ultima", "borrar_primera", "duplicar", "reordenar"])
def test_detecta_filas_borradas_duplicadas_o_reordenadas(bd, extractor, ataque):
    lineas = bd_con_tres_filas(bd, extractor)
    cabecera, filas = lineas[0], lineas[1:]
    if ataque == "borrar_ultima":
        filas = filas[:-1]
    elif ataque == "borrar_primera":
        filas = filas[1:]
    elif ataque == "duplicar":
        filas = filas + [filas[0]]
    elif ataque == "reordenar":
        filas = [filas[2], filas[0], filas[1]]
    escribir_lineas(bd, [cabecera] + filas)
    with pytest.raises(IntegridadBD):
        bd.muestras()


def test_no_se_puede_inscribir_sobre_una_bd_manipulada(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    escribir_lineas(bd, lineas[:-1])
    with pytest.raises(IntegridadBD):
        bd.inscribir("Eva", template_aleatorio(extractor, 5))
    assert bd.ruta.read_text(encoding="utf-8").splitlines() == lineas[:-1]  # no se re-firma


@pytest.mark.parametrize("contenido", [
    "",                                  # vacío
    "nombre,template,hmac\n",            # cabecera distinta
    "id,template,hmac\nAna,f32:AAAA\n",  # faltan columnas
    "id,template,hmac\nAna,f32:AAAA,ab,extra\n",
])
def test_formato_incorrecto(bd, contenido):
    bd.ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(BDCorrupta):
        bd.muestras()


def test_fichero_binario(bd):
    bd.ruta.write_bytes(b"\xff\xfe\x00basura")
    with pytest.raises(BDCorrupta):
        bd.muestras()


def test_firma_no_ascii(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    id_persona, campo, firma = lineas[1].split(",")
    lineas[1] = ",".join([id_persona, campo, "ñ" + firma[1:]])
    escribir_lineas(bd, lineas)
    with pytest.raises(IntegridadBD):
        bd.muestras()


def test_bd_creada_con_otro_extractor(bd, cfg_tmp, extractor):
    bd_con_tres_filas(bd, extractor)
    otro = ExtractorLBP(radio=2, puntos=16)
    with pytest.raises(BDCorrupta, match="otro extractor"):
        BaseDatos(cfg_tmp, otro).muestras()


# --- Cifrado -----------------------------------------------------------------------------

@pytest.fixture
def bd_cifrada(cfg_tmp, extractor):
    return BaseDatos(cfg_con(cfg_tmp, bd={"cifrar_templates": True}), extractor)


def test_cifrado_ida_y_vuelta(bd_cifrada, extractor):
    t = template_aleatorio(extractor, 0)
    bd_cifrada.inscribir("Ana", t)
    campo = bd_cifrada.ruta.read_text().splitlines()[1].split(",")[1]
    assert campo.startswith("gcm:")
    assert np.array_equal(bd_cifrada.templates_de("Ana")[0], t)


def test_cifrado_usa_nonce_distinto_cada_vez(bd_cifrada, extractor):
    t = template_aleatorio(extractor, 0)
    bd_cifrada.inscribir("Ana", t)
    bd_cifrada.inscribir("Ana", t)
    campos = [linea.split(",")[1] for linea in bd_cifrada.ruta.read_text().splitlines()[1:]]
    assert campos[0] != campos[1]


def test_se_leen_filas_planas_y_cifradas_mezcladas(cfg_tmp, bd, bd_cifrada, extractor):
    # Activar el cifrado con una BD ya creada no la rompe: el prefijo dice cómo leer cada fila.
    bd.inscribir("Ana", template_aleatorio(extractor, 0))
    bd_cifrada.inscribir("Luis", template_aleatorio(extractor, 1))
    assert BaseDatos(cfg_tmp, extractor).ids() == ["Ana", "Luis"]


# --- Identificadores ----------------------------------------------------------------------

@pytest.mark.parametrize("id_persona", ["Ana", "José Pérez", "Ñandú_2", "O'Brien", "Ana-María", "persona 51",
                                       "Hwang Min-Hyun", "황민현", "Владимир", "山田たろう", "Ἀθηνᾶ"])
def test_ids_validos(id_persona):
    assert validar_id(id_persona) == id_persona


def test_id_se_normaliza():
    assert validar_id("  Ana  ") == "Ana"
    assert validar_id("José") == "José"  # misma "é" escrita de dos formas


@pytest.mark.parametrize("id_persona", [
    "", "   ", "a" * 101,
    "=HYPERLINK(\"http://x\")", "+1", "-1", "@SUM(A1)",  # inyección de fórmulas en Excel
    "Ana\nLuis", "Ana\x00", "Ana,Luis", "Ana;Luis", "../etc/passwd", "_Ana",
    "\u0410dele", "Keanu R\u0435eves", "Ad\u03b5le",  # homoglifos: А/е cirílicas, ε griega
    123, None,
])
def test_ids_invalidos(id_persona):
    with pytest.raises(IdInvalido):
        validar_id(id_persona)


def test_id_unicode_se_guarda_bien(bd, extractor):
    bd.inscribir("José Pérez", template_aleatorio(extractor, 0))
    assert BaseDatos(bd.cfg, extractor).ids() == ["José Pérez"]


@pytest.mark.parametrize("escrito, clave", [
    ("Lionel Messi", "lionel messi"),
    ("LIONEL  MESSI", "lionel messi"),
    ("Nicolás Maduro", "nicolas maduro"),
    ("Jorge Javier Vázquez", "jorge javier vazquez"),
    ("Hwang Min-Hyun", "hwang min-hyun"),
    ("Muñoz", "munoz"),
])
def test_clave_busqueda(escrito, clave):
    assert clave_busqueda(escrito) == clave


def test_buscar_id_sin_mayusculas_ni_tildes(bd, extractor):
    """M4 de la revisión: el id se encuentra aunque se escriba con otras mayúsculas,
    tildes o espacios; el id guardado (y su HMAC) no cambia."""
    bd.inscribir("José Pérez", template_aleatorio(extractor, 1))
    bd.inscribir("Lionel Messi", template_aleatorio(extractor, 2))
    assert bd.resolver_id("jose  perez") == "José Pérez"
    assert len(bd.templates_de("LIONEL MESSI")) == 1
    assert [c for c, _ in bd.muestras_de("lionel messi")] == [c for c, _ in bd.muestras_de("Lionel Messi")]
    # Inscribir con otra forma añade una muestra a la persona inscrita, no crea otra
    assert bd.inscribir("lionel messi", template_aleatorio(extractor, 3)) == 2
    assert bd.ids() == ["José Pérez", "Lionel Messi"]
    # Una errata de letra sigue sin encontrarse
    assert bd.resolver_id("Lionel Mesi") is None
    with pytest.raises(IdNoInscrito):
        bd.templates_de("Lionel Mesi")
    assert bd.eliminar("jose pérez") == 1 and bd.ids() == ["Lionel Messi"]


def test_resolver_id_con_dos_inscritos_que_solo_difieren_en_tildes(bd):
    """Si ya hubiera "Jose Perez" y "José Pérez", solo vale el id exacto."""
    inscritos = {"Jose Perez", "José Pérez"}
    assert bd.resolver_id("José Pérez", inscritos) == "José Pérez"
    with pytest.raises(IdInvalido, match="exactamente"):
        bd.resolver_id("jose perez", inscritos)


def test_renombrar_no_choca_con_otro_salvo_mayusculas(bd, extractor):
    bd.inscribir("Ana", template_aleatorio(extractor, 1))
    bd.inscribir("Luis", template_aleatorio(extractor, 2))
    with pytest.raises(IdInvalido):
        bd.renombrar("Ana", "LUIS")              # es Luis, aunque cambien las mayúsculas
    bd.renombrar("ana", "Ána")                   # cambiar solo las tildes de uno mismo, sí
    assert bd.ids() == ["Luis", "Ána"]


def test_eliminar_persona(bd, extractor):
    bd_con_tres_filas(bd, extractor)
    assert bd.eliminar("Ana") == 2
    assert bd.ids() == ["Luis"]  # el resto sigue verificando (HMAC re-firmado)
    with pytest.raises(IdNoInscrito):
        bd.eliminar("Ana")


def test_no_se_puede_eliminar_en_una_bd_manipulada(bd, extractor):
    lineas = bd_con_tres_filas(bd, extractor)
    escribir_lineas(bd, lineas[:-1])
    with pytest.raises(IntegridadBD):
        bd.eliminar("Ana")


def test_muestras_de_y_eliminar_una(bd, extractor):
    bd_con_tres_filas(bd, extractor)
    muestras = bd.muestras_de("Ana")
    assert len(muestras) == 2 and muestras[0][0] != muestras[1][0]
    clave = muestras[0][0]
    assert bd.eliminar_muestra("Ana", clave) == 1
    assert [c for c, _ in bd.muestras_de("Ana")] == [muestras[1][0]]
    with pytest.raises(MuestraNoEncontrada):
        bd.eliminar_muestra("Ana", clave)
    with pytest.raises(IdNoInscrito):
        bd.muestras_de("Pepe")


@pytest.mark.parametrize("cifrar", [False, True])
def test_renombrar(cfg_tmp, extractor, cifrar):
    bd = BaseDatos(cfg_con(cfg_tmp, bd={"cifrar_templates": cifrar}), extractor)
    bd_con_tres_filas(bd, extractor)
    antes = bd.templates_de("Ana")
    claves = bd.renombrar("Ana", "Ana López")
    assert bd.ids() == ["Ana López", "Luis"]
    assert all(np.array_equal(a, b) for a, b in zip(antes, bd.templates_de("Ana López")))
    assert len(claves) == 2
    assert (list(claves) == list(claves.values())) is (not cifrar)  # cifrados: claves nuevas
    with pytest.raises(IdInvalido):
        bd.renombrar("Luis", "Ana López")
