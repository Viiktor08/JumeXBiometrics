"""Tests de las contramedidas frente a ataques (ver THREAT_MODEL.md).

Cada test monta una BD temporal, lanza el ataque como lo haría el grupo rival
(solo métodos públicos) y comprueba que la defensa lo para sin estorbar al uso
legítimo. Necesitan los modelos y, la primera vez, la foto de prueba.
"""

import json
import threading

import numpy as np
import pytest

from biometria.bd import VARIABLE_CLAVE
from biometria.config import RAIZ_REPO
from biometria.errores import (CaraYaInscrita, DemasiadosIntentos, IntegridadBD, MuestraIncoherente,
                               SinCara, TemplateNoFirmado)
from biometria.extractores.arcface import MODELO_POR_DEFECTO
from biometria.sistema import SistemaBiometrico
from tests.conftest import cfg_con

import cv2  # noqa: I001

pytestmark = pytest.mark.skipif(not (RAIZ_REPO / MODELO_POR_DEFECTO).is_file(),
                                reason="Falta el modelo ArcFace: python scripts/descargar_modelos.py")


@pytest.fixture
def cfg_tmp(cfg, tmp_path, monkeypatch, detector):
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    return cfg_con(cfg,
                   bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "k.key"},
                   defensa={"coherencia_muestra": 0.20, "exigir_firma_t": False})


# --- Paso 1: envenenar la inscripción ---------------------------------------

def test_coherencia_bloquea_envenenamiento(cfg_tmp, ruta_astronauta, detector, astronauta):
    """Añadir una cara ajena como muestra de una persona existente se rechaza,
    pero otra foto de la misma persona se acepta."""
    sistema = SistemaBiometrico(cfg_tmp)
    sistema.inscribir("Eileen Collins", ruta_astronauta)

    # Otra foto de la MISMA persona (la astronauta con un cambio de brillo leve): se acepta
    alto, ancho = astronauta.shape[:2]
    variacion = cv2.convertScaleAbs(astronauta, alpha=1.08, beta=8)
    r = sistema.inscribir("Eileen Collins", variacion)
    assert r.muestras == 2

    # Una cara AJENA (ruido con estructura, que ArcFace ve lejísimos): se rechaza
    ajena = sistema.extractor.extraer(
        np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    with pytest.raises(MuestraIncoherente):
        sistema.inscribir_t("Eileen Collins", ajena)


def test_coherencia_no_estorba_a_una_persona_nueva(cfg_tmp):
    """La primera muestra de alguien nunca se rechaza: no hay con qué comparar."""
    sistema = SistemaBiometrico(cfg_tmp)
    ajena = sistema.extractor.extraer(
        np.random.default_rng(2).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    assert sistema.inscribir_t("Quien Sea", ajena).muestras == 1


def test_coherencia_desactivada_deja_pasar_el_ataque(cfg, tmp_path, monkeypatch, detector, ruta_astronauta):
    """Con coherencia_muestra: 0 el envenenamiento vuelve a funcionar (prueba de
    que la defensa es la que lo para, no otra cosa)."""
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    cfg_sin = cfg_con(cfg, bd={"ruta": tmp_path / "bd.csv", "ruta_clave": tmp_path / "k.key"},
                      defensa={"coherencia_muestra": 0.0, "exigir_firma_t": False})
    sistema = SistemaBiometrico(cfg_sin)
    sistema.inscribir("Eileen Collins", ruta_astronauta)
    ajena = sistema.extractor.extraer(
        np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8))
    assert sistema.inscribir_t("Eileen Collins", ajena).muestras == 2  # entra sin problema


def _deriva(sistema, victima, atacante, pasos=8):
    """Ataque de la revisión (A4): una cadena de morphs víctima -> atacante añadidos
    como muestras de la víctima. Devuelve cuántos se aceptan."""
    aceptados = 0
    for alfa in np.linspace(0.1, 0.9, pasos):
        morph = (1 - alfa) * victima + alfa * atacante
        try:
            sistema.inscribir_t("Victima", (morph / np.linalg.norm(morph)).astype(np.float32))
            aceptados += 1
        except MuestraIncoherente:
            pass
    return aceptados


def test_deriva_con_morphs_no_cuela_al_atacante(cfg_tmp):
    """Coherencia contra la PRIMERA muestra + ancla: los morphs cercanos a la víctima
    pueden entrar, pero no sirven para que el atacante se verifique como ella."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={
        "coherencia_muestra": 0.35, "ancla_muestra": 0.20, "exigir_firma_t": False}))
    victima, atacante = _vector(30), _vector(31)
    sistema.inscribir_t("Victima", victima)
    aceptados = _deriva(sistema, victima, atacante)
    assert 0 < aceptados < 8  # los que se alejan de la inscripción original se rechazan
    assert not sistema.verificar_t("Victima", atacante, _contar=False).aceptado
    assert sistema.identificar_t(atacante, _contar=False).id is None
    assert sistema.verificar_t("Victima", victima, _contar=False).aceptado  # la víctima sigue


def test_sin_ancla_la_deriva_si_cuela_al_atacante(cfg_tmp):
    """Prueba de que es el ancla la que para el ataque: sin ella, un morph aceptado
    (parecido a víctima y atacante) basta para verificar al atacante."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={
        "coherencia_muestra": 0.35, "ancla_muestra": 0.0, "exigir_firma_t": False}))
    victima, atacante = _vector(30), _vector(31)
    sistema.inscribir_t("Victima", victima)
    _deriva(sistema, victima, atacante)
    assert sistema.verificar_t("Victima", atacante, _contar=False).aceptado


def test_coherencia_compara_con_la_primera_muestra(cfg_tmp):
    """Una muestra parecida a la ÚLTIMA añadida pero no a la primera se rechaza
    (antes bastaba con parecerse a cualquiera: así funcionaba la deriva)."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={
        "coherencia_muestra": 0.35, "ancla_muestra": 0.0, "exigir_firma_t": False}))
    a, b = _vector(40), _vector(41)
    paso1 = 0.6 * a + 0.4 * b
    paso1 /= np.linalg.norm(paso1)
    paso2 = 0.25 * a + 0.75 * b
    paso2 /= np.linalg.norm(paso2)
    sistema.inscribir_t("P", a)
    sistema.inscribir_t("P", paso1)                      # cerca de la primera: entra
    assert float(np.dot(paso2, paso1)) > 0.35            # se parece a la última...
    assert float(np.dot(paso2, a)) < 0.35                # ...pero no a la primera
    with pytest.raises(MuestraIncoherente):
        sistema.inscribir_t("P", paso2)


def test_misma_cara_con_otro_nombre_se_rechaza(cfg_tmp, ruta_astronauta):
    """M3 de la revisión: inscribir la foto de alguien con OTRO nombre se rechaza, y
    su identificación sigue funcionando. Con su propio nombre, la foto sí entra."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"cara_duplicada": 0.60}))
    sistema.inscribir("Eileen Collins", ruta_astronauta)
    with pytest.raises(CaraYaInscrita) as e:
        sistema.inscribir("Copia De Eileen", ruta_astronauta)
    assert "Eileen" not in e.value.mensaje                # no dice a quién se parece
    r = sistema.identificar(ruta_astronauta)
    assert r.id == "Eileen Collins" and not r.ambiguo
    assert sistema.inscribir("Eileen Collins", ruta_astronauta).muestras == 2


def test_sin_la_defensa_el_duplicado_rompe_la_identificacion(cfg_tmp, ruta_astronauta):
    """Prueba de que es `cara_duplicada` la que lo para: sin ella, la víctima sale ambigua."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"cara_duplicada": 0.0}))
    sistema.inscribir("Eileen Collins", ruta_astronauta)
    sistema.inscribir("Copia De Eileen", ruta_astronauta)
    r = sistema.identificar(ruta_astronauta)
    assert r.id is None and r.ambiguo


def test_limite_de_intentos_cuenta_por_persona_aunque_cambie_el_nombre(cfg_tmp):
    """Con la búsqueda sin mayúsculas ni tildes, cambiar cómo se escribe el nombre no
    da un cupo nuevo de intentos _t."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"max_intentos_t": 3, "exigir_firma_t": False}))
    sistema.inscribir_t("Lionel Messi", _vector(5))
    for nombre in ("lionel messi", "LIONEL MESSI", "Lionel Messi"):
        assert sistema.verificar_t(nombre, _vector(5)).id == "Lionel Messi"
    with pytest.raises(DemasiadosIntentos):
        sistema.verificar_t("lionel  messi", _vector(5))


# --- Paso 2: firma de templates (contra templates fabricados en _t) ----------

def test_firma_distingue_template_propio_de_fabricado(cfg_tmp, ruta_astronauta):
    sistema = SistemaBiometrico(cfg_tmp)
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    firma = sistema.firmar(t)

    # Firmado por nosotros: fiable=True
    assert sistema.verificar_t("Eileen", t, firma).fiable is True
    assert sistema.identificar_t(t, firma).fiable is True
    # Sin firma: fiable=None (no se evaluó)
    assert sistema.verificar_t("Eileen", t).fiable is None
    # Firma incorrecta (template fabricado o manipulado): fiable=False
    assert sistema.verificar_t("Eileen", t, "ab" * 32).fiable is False


def test_la_firma_depende_de_la_clave(cfg, tmp_path, monkeypatch, detector, ruta_astronauta):
    """Un template firmado por OTRO sistema (otra clave) no se acepta como fiable."""
    monkeypatch.delenv(VARIABLE_CLAVE, raising=False)
    sin_estricto = {"exigir_firma_t": False}  # aquí se mira `fiable`, no el rechazo
    a = SistemaBiometrico(cfg_con(cfg, bd={"ruta": tmp_path / "a.csv", "ruta_clave": tmp_path / "a.key"},
                                  defensa=sin_estricto))
    b = SistemaBiometrico(cfg_con(cfg, bd={"ruta": tmp_path / "b.csv", "ruta_clave": tmp_path / "b.key"},
                                  defensa=sin_estricto))
    a.inscribir("Eileen", ruta_astronauta)
    b.inscribir("Eileen", ruta_astronauta)
    t = a.extraer(ruta_astronauta)
    firma_de_a = a.firmar(t)
    assert a.verificar_t("Eileen", t, firma_de_a).fiable is True
    assert b.verificar_t("Eileen", t, firma_de_a).fiable is False  # la clave de B es otra


# --- Paso 3: anti hill-climbing (redondeo + límite de intentos) --------------

def test_la_puntuacion_de_t_se_redondea(cfg_tmp, ruta_astronauta):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"decimales_puntuacion_t": 2}))
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    p = sistema.verificar_t("Eileen", t).puntuacion
    assert p == round(p, 2)  # no hay más de 2 decimales de información


def test_limite_de_intentos_frena_el_barrido(cfg_tmp, ruta_astronauta):
    # cara_duplicada 0: aquí se inscribe a propósito la misma cara con dos nombres
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"max_intentos_t": 5, "ventana_intentos_s": 60,
                                                          "cara_duplicada": 0.0}))
    sistema.inscribir("Eileen", ruta_astronauta)
    t = sistema.extraer(ruta_astronauta)
    for _ in range(5):
        sistema.verificar_t("Eileen", t)  # los 5 primeros pasan
    with pytest.raises(DemasiadosIntentos):
        sistema.verificar_t("Eileen", t)  # el sexto, en la misma ventana, se frena
    # El límite es POR id: otra persona tiene su propio cupo
    sistema.inscribir("Otra", ruta_astronauta)
    assert sistema.verificar_t("Otra", t).id == "Otra"


def test_las_operaciones_con_foto_no_gastan_el_cupo(cfg_tmp, ruta_astronauta):
    """verificar/identificar (con foto) no cuentan para el límite: ahí se ve la cara,
    no hay hill-climbing por template."""
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"max_intentos_t": 2}))
    sistema.inscribir("Eileen", ruta_astronauta)
    for _ in range(10):
        sistema.verificar("Eileen", ruta_astronauta)  # nunca se frena
    assert sistema.identificar(ruta_astronauta).id == "Eileen"


# --- Paso 2 (estricto): los _t rechazan lo que no está firmado ----------------

def _vector(semilla: int) -> np.ndarray:
    v = np.random.default_rng(semilla).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def estricto(cfg_tmp):
    return SistemaBiometrico(cfg_con(cfg_tmp, defensa={"exigir_firma_t": True}))


def test_estricto_rechaza_template_sin_firma_o_con_firma_falsa(estricto, ruta_astronauta):
    estricto.inscribir("Eileen", ruta_astronauta)
    fabricado = _vector(1)
    for llamada in (lambda f: estricto.verificar_t("Eileen", fabricado, f),
                    lambda f: estricto.identificar_t(fabricado, f),
                    lambda f: estricto.inscribir_t("Otro", fabricado, f)):
        for firma in (None, "ab" * 32, "no-hex"):
            with pytest.raises(TemplateNoFirmado):
                llamada(firma)


def test_estricto_acepta_los_templates_propios(estricto, ruta_astronauta):
    estricto.inscribir("Eileen", ruta_astronauta)
    t = estricto.extraer(ruta_astronauta)
    firma = estricto.firmar(t)
    r = estricto.verificar_t("Eileen", t, firma)
    assert r.aceptado and r.fiable is True
    assert estricto.identificar_t(t, firma).id == "Eileen"


def test_estricto_rechaza_el_template_copiado_de_la_bd(estricto, ruta_astronauta):
    """Quien LEE la BD saca el template de la víctima, pero no su firma."""
    estricto.inscribir("Eileen", ruta_astronauta)
    copiado = estricto.bd.templates_de("Eileen")[0]
    with pytest.raises(TemplateNoFirmado):
        estricto.verificar_t("Eileen", copiado)


def test_estricto_rechaza_un_template_firmado_y_retocado(estricto, ruta_astronauta):
    """Hill-climbing: cada variación del template pierde la firma del original."""
    estricto.inscribir("Eileen", ruta_astronauta)
    t = estricto.extraer(ruta_astronauta)
    firma = estricto.firmar(t)
    retocado = t.copy()
    retocado[0] += 1e-3
    retocado /= np.linalg.norm(retocado)
    with pytest.raises(TemplateNoFirmado):
        estricto.verificar_t("Eileen", retocado, firma)


# --- Paso 4: margen entre el 1.º y el 2.º (gemelos / dobles) ------------------

def test_margen_marca_ambiguo_un_empate_entre_dos_personas(cfg_tmp):
    a, b = _vector(10), _vector(11)
    medio = (a + b) / np.linalg.norm(a + b)  # igual de parecido a los dos (~0,71)
    for margen, esperado in ((0.10, {None}), (0.0, {"A", "B"})):  # sin margen, decide uno al azar
        cfg = cfg_con(cfg_tmp, bd={"ruta": cfg_tmp.bd.ruta.with_name(f"m{margen}.csv")},
                      defensa={"margen_identificacion": margen, "exigir_firma_t": False})
        sistema = SistemaBiometrico(cfg)
        sistema.inscribir_t("A", a)
        sistema.inscribir_t("B", b)
        r = sistema.identificar_t(medio, _contar=False)
        assert r.id in esperado and r.ambiguo is (margen > 0)
        assert sistema.identificar_t(a, _contar=False).id == "A"  # sin empate, decide


# --- Paso 5: los _sd exigen que la imagen sea una cara ------------------------

def test_sd_rechaza_lo_que_no_es_una_cara(cfg_tmp, astronauta):
    sistema = SistemaBiometrico(cfg_con(cfg_tmp, defensa={"cara_en_sd": True}))
    ruido = np.random.default_rng(0).integers(0, 256, (112, 112, 3), dtype=np.uint8)
    liso = np.full((112, 112, 3), 90, dtype=np.uint8)
    for imagen in (ruido, liso):
        with pytest.raises(SinCara):
            sistema.inscribir_sd("Nadie", imagen)
    # Un recorte de cara de verdad sí entra
    caja = sistema.detector.detectar(astronauta)[0].caja
    x, y, w, h = caja
    assert sistema.inscribir_sd("Eileen", astronauta[y:y + h, x:x + w]).muestras == 1


# --- Paso 6: clave aparte, cifrado, sello anti-rollback y bloqueo --------------

@pytest.fixture
def cfg_sello(cfg_tmp, tmp_path):
    """BD en tmp/datos y la clave (con el sello) en otra carpeta, como en config.yaml."""
    return cfg_con(cfg_tmp, bd={"ruta": tmp_path / "datos" / "bd.csv",
                                "ruta_clave": tmp_path / "secreto" / "clave.key",
                                "cifrar_templates": True, "anti_rollback": True},
                   defensa={"exigir_firma_t": False})


def test_rollback_a_una_copia_antigua_se_detecta(cfg_sello):
    sistema = SistemaBiometrico(cfg_sello)
    sistema.inscribir_t("A", _vector(1))
    copia = sistema.bd.ruta.read_bytes()           # el atacante guarda esta versión
    sistema.inscribir_t("B", _vector(2))
    sistema.bd.eliminar("A")                       # el admin borra a A...
    sistema.bd.ruta.write_bytes(copia)             # ...y el atacante restaura la copia
    with pytest.raises(IntegridadBD, match="rollback"):
        sistema.bd.ids()


def test_borrar_la_bd_entera_se_detecta(cfg_sello):
    sistema = SistemaBiometrico(cfg_sello)
    sistema.inscribir_t("A", _vector(1))
    sistema.bd.ruta.unlink()
    with pytest.raises(IntegridadBD, match="desaparecido"):
        sistema.bd.ids()


def _rollback_borrando_el_sello(sistema):
    """B2 de la revisión: guardar una copia, seguir, borrar el sello y restaurar la copia."""
    sistema.inscribir_t("A", _vector(1))
    copia = sistema.bd.ruta.read_bytes()
    sistema.inscribir_t("B", _vector(2))
    sistema.bd.ruta_sello().unlink()
    sistema.bd.ruta.write_bytes(copia)


def test_borrar_el_sello_y_restaurar_una_copia_se_detecta(cfg_sello):
    sistema = SistemaBiometrico(cfg_con(cfg_sello, bd={"sello_estricto": True}))
    _rollback_borrando_el_sello(sistema)
    with pytest.raises(IntegridadBD, match="falta su sello"):
        sistema.bd.ids()
    # Aceptarla es una decisión explícita del admin (python -m biometria sellar)
    assert sistema.bd.sellar() == 1
    assert sistema.bd.ids() == ["A"]


def test_sin_sello_estricto_se_confia_en_la_bd_sin_sello(cfg_sello):
    """El comportamiento anterior (TOFU), que era el agujero: la copia antigua se acepta."""
    sistema = SistemaBiometrico(cfg_con(cfg_sello, bd={"sello_estricto": False}))
    _rollback_borrando_el_sello(sistema)
    assert sistema.bd.ids() == ["A"]


def test_sellar_no_acepta_filas_manipuladas(cfg_sello):
    """sellar() verifica el HMAC de cada fila: sin la clave no se puede sellar una BD falsa."""
    sistema = SistemaBiometrico(cfg_con(cfg_sello, bd={"sello_estricto": True}))
    sistema.inscribir_t("A", _vector(1))
    sistema.bd.ruta_sello().unlink()
    lineas = sistema.bd.ruta.read_text(encoding="utf-8").splitlines()
    lineas[1] = lineas[1].replace("A,", "Intruso,", 1)
    sistema.bd.ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    with pytest.raises(IntegridadBD):
        sistema.bd.sellar()


def test_el_sello_manipulado_se_detecta(cfg_sello):
    sistema = SistemaBiometrico(cfg_sello)
    sistema.inscribir_t("A", _vector(1))
    ruta = sistema.bd.ruta_sello()
    datos = json.loads(ruta.read_text())
    datos["sha256"] = "0" * 64
    ruta.write_text(json.dumps(datos))
    with pytest.raises(IntegridadBD, match="sello"):
        sistema.bd.ids()


def test_lectura_a_mitad_de_escritura_no_da_falso_rollback(cfg_sello):
    """Sello en dos fases: entre anunciar la versión nueva y confirmarla, un lector
    acepta tanto la anterior como la nueva."""
    sistema = SistemaBiometrico(cfg_sello)
    sistema.inscribir_t("A", _vector(1))
    bd = sistema.bd
    anterior = bd.ruta.read_bytes()
    bd._escribir_sello(bd._resumen(anterior), pendiente="f" * 64)  # escritura en curso
    assert bd.ids() == ["A"]


def test_bd_cifrada_no_deja_leer_los_templates_y_la_clave_va_aparte(cfg_sello):
    sistema = SistemaBiometrico(cfg_sello)
    sistema.inscribir_t("A", _vector(1))
    texto = sistema.bd.ruta.read_text()
    assert "f32:" not in texto and "gcm:" in texto
    assert not sistema.bd.clave_junto_a_bd()
    assert not any(p.suffix == ".key" for p in sistema.bd.ruta.parent.iterdir())


def test_recodificar_cifra_una_bd_en_claro(cfg_sello):
    en_claro = cfg_con(cfg_sello, bd={"cifrar_templates": False})
    a = SistemaBiometrico(en_claro)
    a.bd.inscribir("A", _vector(1))  # directo a la BD: sin la coherencia del paso 1
    a.bd.inscribir("A", _vector(2))
    antes = a.bd.templates_de("A")
    b = SistemaBiometrico(cfg_sello)
    cambios = b.bd.recodificar()
    assert len(cambios["A"]) == 2 and all(v != k for k, v in cambios["A"].items())
    assert "f32:" not in b.bd.ruta.read_text()
    assert all(np.array_equal(x, y) for x, y in zip(antes, b.bd.templates_de("A")))


def test_dos_procesos_escribiendo_a_la_vez_no_pierden_muestras(cfg_sello):
    """Con el bloqueo, leer-modificar-escribir es atómico: no se pisan."""
    def inscribir(n):
        sistema = SistemaBiometrico(cfg_sello)  # instancia propia, como otro proceso
        for i in range(8):
            sistema.bd.inscribir(f"P{n}-{i}", _vector(100 * n + i))
    SistemaBiometrico(cfg_sello).bd.inscribir("Inicial", _vector(0))  # crea la clave
    hilos = [threading.Thread(target=inscribir, args=(n,)) for n in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(SistemaBiometrico(cfg_sello).bd.ids()) == 1 + 4 * 8
