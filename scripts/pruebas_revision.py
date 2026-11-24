"""Batería de ataques y medidas contra el sistema real (ver docs/EVALUACION.md).

Se ejecuta DENTRO del contenedor y no toca nada real:
  - BD, clave y sello en una carpeta temporal (/tmp/revision_*), clave por variable de entorno;
  - las fotos de /app/Dataset solo se leen.

    docker compose up -d
    docker compose exec -T web python scripts/pruebas_revision.py > resultados_revision.json

Sirve también de punto de partida para evaluar el sistema del grupo rival (fase 3):
cada sección es un ataque o una medida independiente.
"""
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from pathlib import Path

os.environ["BIOMETRIA_HMAC_KEY"] = secrets.token_hex(32)   # clave temporal: nunca ~/.jumex
TMP = Path(tempfile.mkdtemp(prefix="revision_"))
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402
from biometria.config import cargar_config  # noqa: E402  (antes que cv2)
from biometria.sistema import SistemaBiometrico  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402
from biometria.bd import BaseDatos, validar_id  # noqa: E402
from preparar_dataset import nombre_de_fichero  # noqa: E402
import cv2  # noqa: E402

R = {}
T0 = time.time()


def cfg_con(cfg, **secc):
    return replace(cfg, **{k: replace(getattr(cfg, k), **v) for k, v in secc.items()})


def seccion(nombre):
    def deco(f):
        t = time.time()
        try:
            R[nombre] = f()
        except Exception as e:  # una sección rota no tumba las demás
            R[nombre] = {"EXCEPCION": f"{type(e).__name__}: {e}", "traza": traceback.format_exc()[-800:]}
        R[nombre] = R[nombre] if isinstance(R[nombre], dict) else {"valor": R[nombre]}
        R[nombre]["_segundos"] = round(time.time() - t, 1)
        print(f"[ok] {nombre} ({R[nombre]['_segundos']} s)", file=sys.stderr, flush=True)
        return f
    return deco


def intenta(f):
    try:
        return f()
    except ErrorBiometrico as e:
        return f"ERROR {e.codigo}"


def r4(x):
    return None if x is None else round(float(x), 4)


def resumen(v):
    v = [x for x in v if isinstance(x, (int, float))]
    if not v:
        return None
    a = np.array(v, dtype=float)
    return {"n": len(a), "min": r4(a.min()), "media": r4(a.mean()), "max": r4(a.max())}


BASE = cargar_config("/app/config_lens.yaml")
CFG = cfg_con(BASE, bd={"ruta": TMP / "bd.csv", "ruta_clave": TMP / "clave" / "k.key"})
S = SistemaBiometrico(CFG)
DS = Path("/app/Dataset")
FOTOS = sorted(f for f in DS.iterdir() if f.is_file() and not f.name.startswith("."))
IDS = {f: nombre_de_fichero(f.name) for f in FOTOS}
IMG = {}
UMB_V, UMB_I = CFG.umbrales.verificacion, CFG.umbrales.identificacion


@seccion("00_entorno")
def _():
    return {"python": sys.version.split()[0], "numpy": np.__version__, "opencv": cv2.__version__,
            "modelos": sorted(p.name for p in Path("/app/models").iterdir()),
            "umbrales": [UMB_V, UMB_I], "defensa": CFG.defensa.__dict__, "fotos_dataset": len(FOTOS)}


@seccion("01_dataset_deteccion")
def _():
    filas, errores = [], {}
    for f in FOTOS:
        cab = f.read_bytes()[:16]
        real = ("jpeg" if cab[:3] == b"\xff\xd8\xff" else "png" if cab[:4] == b"\x89PNG"
                else "webp" if cab[8:12] == b"WEBP" else "avif" if b"avif" in cab else "otro")
        try:
            img = cargar_imagen(f, CFG.limites)
            IMG[f] = img
            dets = S.detector.detectar(img)
            p = S.detector.procesar_deteccion(img, dets[0])
            filas.append({"f": f.name, "formato_real": real, "ext": f.suffix, "px": list(img.shape[:2]),
                          "caras": len(dets), "conf": r4(dets[0].confianza),
                          "lado_cara": int(min(dets[0].caja[2:])), "nitidez": round(p.nitidez, 1)})
        except ErrorBiometrico as e:
            errores[f.name] = e.codigo
    return {"formato_no_coincide_con_extension": [x["f"] + " -> " + x["formato_real"] for x in filas
                                                  if x["formato_real"] not in x["ext"].lower().replace("jpg", "jpeg")],
            "con_varias_caras": [(x["f"], x["caras"]) for x in filas if x["caras"] > 1],
            "cara_mas_pequena": sorted(filas, key=lambda x: x["lado_cara"])[:3],
            "menos_nitidas": sorted(filas, key=lambda x: x["nitidez"])[:3],
            "menor_confianza": sorted(filas, key=lambda x: x["conf"])[:3],
            "errores": errores}


@seccion("02_inscripcion_49")
def _():
    t, fallos = [], {}
    for f in FOTOS:
        a = time.time()
        r = intenta(lambda: S.inscribir(IDS[f], f))
        t.append(time.time() - a)
        if isinstance(r, str):
            fallos[f.name] = r
    shutil.copy(CFG.bd.ruta, TMP / "bd_49.csv")
    lineas = CFG.bd.ruta.read_text().splitlines()[1:]
    return {"inscritas": len(S.bd.ids()), "fallos": fallos, "ms_medio": round(1000 * np.mean(t)),
            "todas_cifradas_gcm": all(l.split(",")[1].startswith("gcm:") for l in lineas)}


@seccion("03_foto_exacta_del_repo")
def _():
    v, ident, amb, mal = [], 0, 0, []
    t_v, t_i = [], []
    for f in FOTOS:
        a = time.time(); r = intenta(lambda: S.verificar(IDS[f], f)); t_v.append(time.time() - a)
        if isinstance(r, str):
            mal.append((f.name, r)); continue
        v.append(r.puntuacion)
        a = time.time(); i = S.identificar(f); t_i.append(time.time() - a)
        ident += i.id == IDS[f]; amb += i.ambiguo
        if i.id != IDS[f]:
            mal.append((f.name, i.id, r4(i.puntuacion)))
    return {"verificar_puntuacion": resumen(v), "aceptadas": sum(x >= UMB_V for x in v),
            "identificadas_bien": ident, "ambiguas": amb, "fallos_identificar": mal,
            "ms_verificar": round(1000 * np.mean(t_v)), "ms_identificar": round(1000 * np.mean(t_i))}


@seccion("04_impostores_entre_los_49")
def _():
    ids = sorted(S.bd.por_id())
    M = np.stack([S.bd.por_id()[i][0] for i in ids]).astype(np.float64)
    C = np.clip(M @ M.T, 0, None)
    iu = np.triu_indices(len(ids), 1)
    vals = C[iu]
    orden = np.argsort(-vals)[:8]
    return {"pares": len(vals), "resumen": resumen(vals),
            "pares_por_encima": {u: int((vals >= u).sum()) for u in (0.15, 0.20, 0.30, 0.35, 0.40)},
            "pares_mas_parecidos": [(ids[iu[0][k]], ids[iu[1][k]], r4(vals[k])) for k in orden]}


def transformaciones(img, det):
    h, w = img.shape[:2]
    x, y, bw, bh = det.caja
    ojos = det.landmarks[:2]
    out = {}
    out["jpeg_q30"] = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 30])[1], 1)
    out["mitad_tamano"] = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    out["espejo"] = img[:, ::-1].copy()
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 12, 1.0)
    out["girada_12"] = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    out["oscura_gamma2"] = np.clip(((img / 255.0) ** 2.0) * 255, 0, 255).astype(np.uint8)
    out["clara_gamma05"] = np.clip(((img / 255.0) ** 0.5) * 255, 0, 255).astype(np.uint8)
    out["gris"] = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    out["desenfoque_leve"] = cv2.GaussianBlur(img, (5, 5), 0)
    gafas = img.copy()
    yo = int(ojos[:, 1].mean()); m = int(0.12 * bh)
    cv2.rectangle(gafas, (int(x), yo - m), (int(x + bw), yo + m), (0, 0, 0), -1)
    out["tapar_ojos"] = gafas
    masc = img.copy()
    cv2.rectangle(masc, (int(x), int(det.landmarks[2][1])), (int(x + bw), int(y + bh)), (200, 200, 200), -1)
    out["mascarilla"] = masc
    return out


@seccion("05_robustez_variantes_de_la_foto")
def _():
    res = {}
    for f in FOTOS:
        img = IMG.get(f)
        if img is None:
            continue
        det = S.detector.detectar(img)[0]
        for nombre, var in transformaciones(img, det).items():
            r = intenta(lambda: S.verificar(IDS[f], var))
            res.setdefault(nombre, []).append(r if isinstance(r, str) else r.puntuacion)
    return {k: {"puntuacion": resumen(v), "aceptadas": sum(isinstance(x, float) and x >= UMB_V for x in v),
                "errores": sorted({x for x in v if isinstance(x, str)})} for k, v in res.items()}


@seccion("06_metodos_sd")
def _():
    v_m, v_s, ok_i, bg = [], [], 0, []
    for f in FOTOS:
        img = IMG.get(f)
        if img is None:
            continue
        x, y, w, h = S.detector.detectar(img)[0].caja
        m = int(0.15 * max(w, h))
        recorte = img[max(0, y - m):y + h + m, max(0, x - m):x + w + m]
        justo = img[max(0, y):y + h, max(0, x):x + w]
        r = intenta(lambda: S.verificar_sd(IDS[f], recorte)); v_m.append(r if isinstance(r, str) else r.puntuacion)
        r = intenta(lambda: S.verificar_sd(IDS[f], justo)); v_s.append(r if isinstance(r, str) else r.puntuacion)
        i = intenta(lambda: S.identificar_sd(recorte)); ok_i += (not isinstance(i, str)) and i.id == IDS[f]
        H, W = img.shape[:2]
        if x > 120 and y > 120:  # esquina de fondo sin cara
            bg.append(intenta(lambda: S.verificar_sd(IDS[f], img[0:112, 0:112])))
    ruido = (np.random.default_rng(0).random((112, 112, 3)) * 255).astype(np.uint8)
    return {"recorte_con_margen": resumen(v_m), "aceptados_con_margen": sum(isinstance(x, float) and x >= UMB_V for x in v_m),
            "recorte_justo_caja": resumen(v_s), "aceptados_justo": sum(isinstance(x, float) and x >= UMB_V for x in v_s),
            "errores": sorted({x for x in v_m + v_s if isinstance(x, str)}),
            "identificar_sd_bien": ok_i,
            "fondo_sin_cara": {"probadas": len(bg), "rechazadas": sum(isinstance(x, str) for x in bg),
                               "aceptadas_como_la_persona": sum((not isinstance(x, str)) and x.aceptado for x in bg)},
            "ruido": str(intenta(lambda: S.verificar_sd(IDS[FOTOS[0]], ruido)))}


@seccion("07_metodos_t_y_firma")
def _():
    f = FOTOS[0]; idp = IDS[f]
    t = S.extraer(f); firma = S.firmar(t)
    copia_bd = S.bd.templates_de(idp)[0]
    retocado = t.copy(); retocado[0] += 1e-3; retocado /= np.linalg.norm(retocado)
    out = {
        "verificar_t_firmado": str(intenta(lambda: S.verificar_t(idp, t, firma))),
        "verificar_t_sin_firma": str(intenta(lambda: S.verificar_t(idp, t))),
        "verificar_t_copia_de_la_bd": str(intenta(lambda: S.verificar_t(idp, copia_bd))),
        "verificar_t_retocado_con_firma_vieja": str(intenta(lambda: S.verificar_t(idp, retocado, firma))),
        "identificar_t_firmado": str(intenta(lambda: S.identificar_t(t, firma))),
        "inscribir_t_sin_firma": str(intenta(lambda: S.inscribir_t("Nuevo Sin Firma", t))),
        "template_dimension_mala": str(intenta(lambda: S.verificar_t(idp, np.ones(10, np.float32), firma))),
        "template_nan": str(intenta(lambda: S.verificar_t(idp, np.full(512, np.nan, np.float32), firma))),
    }
    S2 = SistemaBiometrico(CFG)  # contador nuevo
    n = 0
    for n in range(1, 80):
        if isinstance(intenta(lambda: S2.verificar_t(idp, t, firma)), str):
            break
    out["limite_intentos_t_salta_en"] = n
    S3 = SistemaBiometrico(CFG)
    out["nuevo_proceso_reinicia_contador"] = str(intenta(lambda: S3.verificar_t(idp, t, firma)))[:60]
    return out


@seccion("08_hill_climbing")
def _():
    out = {}
    objetivo = IDS[FOTOS[1]]
    M = np.stack(S.bd.templates_de(objetivo)).astype(np.float64)
    for dec in (0, 2):
        rng = np.random.default_rng(1)
        t = rng.standard_normal(512); t /= np.linalg.norm(t)
        orac = lambda v: (lambda s: round(s, dec) if dec else s)(max(float(np.max(M @ v)), 0.0))
        exacta = lambda v: max(float(np.max(M @ v)), 0.0)
        mejor, q, a = orac(t), 1, time.time()
        while q < 200000 and exacta(t) < UMB_V:
            c = t + (0.12 / np.sqrt(512)) * rng.standard_normal(512); c /= np.linalg.norm(c)
            s = orac(c); q += 1
            if s >= mejor:
                t, mejor = c, s
        out[f"decimales_{dec}"] = {"consultas": q, "segundos": round(time.time() - a, 2), "exacta_final": r4(exacta(t))}
    final = t.astype(np.float32); final /= np.linalg.norm(final)
    sin_firma = SistemaBiometrico(cfg_con(CFG, defensa={"exigir_firma_t": False, "max_intentos_t": 0}))
    out["confirmado_con_verificar_t_sin_defensa_firma"] = str(intenta(lambda: sin_firma.verificar_t(objetivo, final)))
    out["con_defensa_firma_activa"] = str(intenta(lambda: S.verificar_t(objetivo, final)))
    out["nota"] = "oráculo = misma fórmula que verificar_t (max coseno, recortado a 0, redondeo); se confirma al final con el método real"
    return out


@seccion("09_ataques_a_la_bd")
def _():
    bd = CFG.bd.ruta
    original = bd.read_bytes()
    lineas = original.decode().splitlines()
    sello = S.bd.ruta_sello()
    sello_bytes = sello.read_bytes()
    out = {}

    def prueba(nombre, contenido):
        bd.write_bytes(contenido)
        out[nombre] = str(intenta(lambda: len(BaseDatos(CFG, S.extractor).ids())))
        bd.write_bytes(original); sello.write_bytes(sello_bytes)

    l = lineas.copy(); c = list(l[1]); c[40] = "A" if c[40] != "A" else "B"; l[1] = "".join(c)
    prueba("cambiar_un_caracter_del_template", ("\n".join(l) + "\n").encode())
    l = lineas.copy(); l[1], l[2] = l[2], l[1]
    prueba("intercambiar_dos_filas", ("\n".join(l) + "\n").encode())
    l = lineas.copy(); del l[5]
    prueba("borrar_una_fila", ("\n".join(l) + "\n").encode())
    l = lineas.copy(); l.insert(3, l[3])
    prueba("duplicar_una_fila", ("\n".join(l) + "\n").encode())
    l = lineas.copy(); p1 = l[1].split(","); p2 = l[2].split(","); l[1] = ",".join([p1[0], p2[1], p2[2]])
    prueba("mover_template_a_otra_persona", ("\n".join(l) + "\n").encode())
    l = lineas.copy(); p = l[1].split(","); l[1] = ",".join(["Intruso", p[1], p[2]])
    prueba("renombrar_fila", ("\n".join(l) + "\n").encode())
    prueba("formula_excel_en_id", ("\n".join(lineas[:1] + ["=HYPERLINK(\"x\")," + lineas[1].split(",", 1)[1]] + lineas[2:]) + "\n").encode())
    # rollback: BD antigua (48 personas) que fue válida
    S.bd.eliminar(IDS[FOTOS[-1]])
    vieja = bd.read_bytes()
    S.inscribir(IDS[FOTOS[-1]], FOTOS[-1])
    original = bd.read_bytes(); sello_bytes = sello.read_bytes()
    prueba("rollback_a_copia_antigua_valida", vieja)
    bd.write_bytes(vieja); sello.unlink()
    out["rollback_borrando_el_sello"] = str(intenta(lambda: len(BaseDatos(CFG, S.extractor).ids())))
    bd.write_bytes(original); sello.write_bytes(sello_bytes)
    bd.unlink()
    out["borrar_la_bd"] = str(intenta(lambda: len(BaseDatos(CFG, S.extractor).ids())))
    bd.write_bytes(original)
    clave = os.environ["BIOMETRIA_HMAC_KEY"]; os.environ["BIOMETRIA_HMAC_KEY"] = secrets.token_hex(32)
    out["otra_clave"] = str(intenta(lambda: len(BaseDatos(CFG, S.extractor).ids())))
    os.environ["BIOMETRIA_HMAC_KEY"] = clave
    out["estado_final_restaurado"] = len(BaseDatos(CFG, S.extractor).ids())
    return out


@seccion("10_envenenamiento_y_deriva")
def _():
    out = {"umbral_coherencia": CFG.defensa.coherencia_muestra, "pares": []}
    out["foto_ajena_como_muestra"] = str(intenta(lambda: S.inscribir(IDS[FOTOS[0]], FOTOS[1])))
    pares = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13), (14, 15)]
    for k, (ia, ib) in enumerate(pares):
        A, B = FOTOS[ia], FOTOS[ib]
        if A not in IMG or B not in IMG:
            continue
        cfg_k = cfg_con(CFG, bd={"ruta": TMP / f"deriva_{k}.csv", "ruta_clave": TMP / "clave" / "k.key"})
        Sk = SistemaBiometrico(cfg_k)
        Sk.inscribir(IDS[A], A)
        ca, cb = Sk.detector.procesar(IMG[A]).cara, Sk.detector.procesar(IMG[B]).cara
        b_otra = cv2.imdecode(cv2.imencode(".jpg", np.clip(IMG[B][:, ::-1] * 1.1, 0, 255).astype(np.uint8),
                                           [cv2.IMWRITE_JPEG_QUALITY, 60])[1], 1)
        antes = Sk.verificar(IDS[A], b_otra).puntuacion
        pasos, ultimo = 0, None
        for alfa in np.linspace(0.12, 0.96, 8):
            t = Sk.extractor.extraer(cv2.addWeighted(ca, 1 - alfa, cb, alfa, 0))
            r = intenta(lambda: Sk.inscribir_t(IDS[A], t, Sk.firmar(t)))
            if isinstance(r, str):
                ultimo = f"{r} en alfa={alfa:.2f}"
                break
            pasos += 1
        despues = Sk.verificar(IDS[A], b_otra)
        out["pares"].append({"victima": IDS[A], "atacante": IDS[B], "morphs_aceptados": pasos, "corte": ultimo,
                             "atacante_como_victima_antes": r4(antes), "despues": r4(despues.puntuacion),
                             "atacante_aceptado_despues": despues.aceptado,
                             "victima_sigue_aceptada": Sk.verificar(IDS[A], A).aceptado})
    out["ataques_con_exito"] = sum(p["atacante_aceptado_despues"] for p in out["pares"])
    return out


@seccion("11_ids_y_nombres")
def _():
    f = FOTOS[0]; idp = IDS[f]
    import unicodedata
    out = {
        "minusculas": str(intenta(lambda: S.verificar(idp.lower(), f))),
        "espacio_final": str(intenta(lambda: S.verificar(idp + " ", f)))[:80],
        "doble_espacio": str(intenta(lambda: S.verificar(idp.replace(" ", "  "), f))),
    }
    for nombre, v in {"formula_excel": "=CMD()", "salto_linea": "Ana\nLópez", "101_caracteres": "a" * 101,
                      "ruta": "../../etc/passwd", "vacio": "   ", "coma": "Ana, López", "comillas": 'Ana "X"',
                      "emoji": "Ana 😀", "jose_nfd_igual_nfc": unicodedata.normalize("NFD", "José")}.items():
        try:
            out[nombre] = validar_id(v) == "José" if nombre == "jose_nfd_igual_nfc" else f"aceptado: {validar_id(v)!r}"
        except ErrorBiometrico as e:
            out[nombre] = f"ERROR {e.codigo}"
    return out


@seccion("12_entradas_y_calidad")
def _():
    f = FOTOS[0]; img = IMG[f]; idp = IDS[f]
    det = S.detector.detectar(img)[0]
    escala = 40 / min(det.caja[2:])
    pequena = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    borrosa = cv2.GaussianBlur(img, (0, 0), 6)
    doble = np.hstack([img, img])
    estricta = SistemaBiometrico(cfg_con(CFG, detector={"varias_caras": "error"}))
    grande = np.zeros((5001, 5001, 3), np.uint8)
    return {
        "basura": str(intenta(lambda: S.verificar(idp, b"no es una imagen"))),
        "vacio": str(intenta(lambda: S.verificar(idp, b""))),
        "png_25MP+": str(intenta(lambda: S.verificar(idp, cv2.imencode(".png", grande)[1].tobytes()))),
        "cara_pequena_40px": str(intenta(lambda: S.verificar(idp, pequena))),
        "muy_borrosa": str(intenta(lambda: S.verificar(idp, borrosa))),
        "dos_caras_config_web(mayor)": str(intenta(lambda: S.verificar(idp, doble)))[:90],
        "dos_caras_config_cli(error)": str(intenta(lambda: estricta.verificar(idp, doble))),
        "imagen_negra": str(intenta(lambda: S.verificar(idp, np.zeros((400, 400, 3), np.uint8)))),
    }


@seccion("13_cli")
def _():
    texto = Path("/app/config_lens.yaml").read_text()
    (TMP / "cli").mkdir()
    texto = (texto.replace("data/lens/bd.csv", str(TMP / "cli" / "bd.csv"))
             .replace("~/.jumex/clave_lens.key", str(TMP / "cli" / "clave.key"))
             .replace("models/", "/app/models/"))
    y = TMP / "cli" / "cfg.yaml"; y.write_text(texto)
    f = str(FOTOS[0]); idp = IDS[FOTOS[0]]
    npy = TMP / "cli" / "t.npy"

    def cli(*args):
        p = subprocess.run([sys.executable, "-m", "biometria", "--config", str(y), *args],
                           capture_output=True, text=True, cwd="/app", timeout=120)
        return {"salida": p.returncode, "json": p.stdout.strip()[:220] or p.stderr.strip()[-220:]}

    out = {}
    out["inscribir"] = cli("inscribir", "--id", idp, "--foto", f)
    out["verificar"] = cli("verificar", "--id", idp, "--foto", f)
    out["identificar"] = cli("identificar", "--foto", f)
    out["verificar_id_inexistente"] = cli("verificar", "--id", "Nadie", "--foto", f)
    out["extraer"] = cli("extraer", "--foto", f, "--salida", str(npy))
    out["verificar_t_con_sig"] = cli("verificar_t", "--id", idp, "--template", str(npy))
    Path(str(npy) + ".sig").unlink()
    out["verificar_t_sin_sig"] = cli("verificar_t", "--id", idp, "--template", str(npy))
    out["bd"] = cli("bd")
    return out


@seccion("14_duplicado_con_otro_nombre")
def _():
    f = FOTOS[0]; idp = IDS[f]
    jpg = cv2.imdecode(cv2.imencode(".jpg", IMG[f], [cv2.IMWRITE_JPEG_QUALITY, 50])[1], 1)
    out = {"umbral_cara_duplicada": CFG.defensa.cara_duplicada,
           "antes": {"id": S.identificar(f).id, "ambiguo": S.identificar(f).ambiguo},
           "con_defensa_misma_foto": str(intenta(lambda: S.inscribir("Copia De Prueba", f))),
           "con_defensa_recomprimida": str(intenta(lambda: S.inscribir("Copia De Prueba", jpg)))}
    r = S.identificar(f)
    out["identificar_despues"] = {"id": r.id, "ambiguo": r.ambiguo}
    # Sin la defensa (BD aparte): así era antes
    cfg_sin = cfg_con(CFG, bd={"ruta": TMP / "dup.csv", "ruta_clave": TMP / "clave" / "k.key"},
                      defensa={"cara_duplicada": 0.0})
    Ssin = SistemaBiometrico(cfg_sin)
    Ssin.inscribir(idp, f)
    Ssin.inscribir("Copia De Prueba", f)
    r = Ssin.identificar(f)
    out["sin_defensa_identificar"] = {"id": r.id, "ambiguo": r.ambiguo}
    return out


R["_total_segundos"] = round(time.time() - T0, 1)
shutil.rmtree(TMP, ignore_errors=True)
print(json.dumps(R, ensure_ascii=False, indent=1, default=str))
