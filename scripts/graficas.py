"""Evaluación para la memoria: curvas DET/ROC/CMC, calibración y test separados, y
la persona del equipo (Lionel Messi) con fotos nuevas que nunca se inscriben.

Uso (en la imagen de pruebas, que trae matplotlib; el compose monta Dataset/,
fotos_prueba/, docs/graficas/ y la caché de LFW):
    docker compose --profile pruebas run --rm --entrypoint python pruebas scripts/graficas.py

    python scripts/graficas.py --personas 300 --fotos 4      # en local, con matplotlib

Qué hace (no toca la BD ni la clave: trabaja solo con templates en memoria):

1. LFW, separando PERSONAS en dos mitades disjuntas:
   - calibración: con ella se calcula el umbral del EER y el de FAR 0,1 %;
   - test: con ella se miden FAR/FRR de esos umbrales y de los de config.yaml.
   Así las cifras de test no salen de los mismos datos con los que se eligió el umbral.
2. Curvas sobre la mitad de test: distribuciones genuino/impostor, DET, ROC y CMC
   (rank-k en identificación cerrada) y el compromiso FPIR/FNIR de la identificación
   abierta (desconocidos aceptados frente a inscritos no identificados).
3. Galería real: las 50 fotos de Dataset/ como BD y las fotos de fotos_prueba/messi/
   (otras épocas, otras cámaras) como consultas. Mide si Messi se verifica y se
   identifica con fotos que el sistema no ha visto, y separación con los otros 49.

Escribe las figuras en docs/graficas/ y las cifras en docs/graficas/resultados.json.
"""

import argparse
import json
import random
import sys
from dataclasses import replace
from pathlib import Path

import os

import numpy as np

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")  # el usuario del contenedor no tiene ~/.cache escribible

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from biometria.config import cargar_config  # noqa: E402
from biometria.detector import Detector  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.extractores import crear_extractor  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402
from evaluar import eer, far_frr, templates_lfw, umbral_para_far  # noqa: E402
from preparar_dataset import EXTENSIONES, nombre_de_fichero  # noqa: E402

PERSONA_EQUIPO = "Lionel Messi"


# --------------------------------------------------------------------------- #
# Cálculos
# --------------------------------------------------------------------------- #
def pares(extractor, conocidas):
    """Puntuaciones genuinas e impostoras de todos los pares de fotos."""
    etiquetas, ts = [], []
    for persona, lista in conocidas.items():
        etiquetas += [persona] * len(lista)
        ts += lista
    m = np.stack(ts)
    sim = np.clip(m @ m.T, 0.0, None)  # = extractor.comparar (coseno de vectores normalizados, >= 0)
    iu = np.triu_indices(len(ts), k=1)
    misma = np.array(etiquetas)[:, None] == np.array(etiquetas)[None, :]
    return sim[iu][misma[iu]], sim[iu][~misma[iu]]


def curva(genuinas, impostoras, n=400):
    """FAR y FRR para umbrales de 0 a 1."""
    umbrales = np.linspace(0.0, 1.0, n)
    far = np.array([np.mean(impostoras >= u) for u in umbrales])
    frr = np.array([np.mean(genuinas < u) for u in umbrales])
    return umbrales, far, frr


def identificacion(conocidas, anonimas, distractores=(), rangos=20):
    """CMC y mejores puntuaciones para la identificación abierta.

    - CMC (identificación cerrada): galería = primera foto de cada persona MÁS los
      `distractores` (caras de otras personas, sin consultas propias), que hacen la
      búsqueda más difícil, como en una BD grande. El resto de fotos son consultas.
    - FNIR/FPIR (abierta): galería = solo los inscritos; los `anonimas` (personas que
      no están) hacen de desconocidos."""
    nombres = list(conocidas)
    galeria = np.stack([conocidas[p][0] for p in nombres])
    ampliada = np.vstack([galeria, *[d[None, :] for d in distractores]]) if len(distractores) else galeria
    posiciones, mejores_inscritos = [], []
    for i, p in enumerate(nombres):
        for t in conocidas[p][1:]:
            orden = np.argsort(-np.clip(ampliada @ t, 0.0, None))
            posiciones.append(int(np.where(orden == i)[0][0]) + 1)
            s = np.clip(galeria @ t, 0.0, None)
            # para FNIR: se identifica bien si el 1.º es el bueno y supera el umbral
            mejores_inscritos.append(float(s[i]) if int(np.argmax(s)) == i else -1.0)
    mejores_anonimos = [float(np.clip(galeria @ t, 0.0, None).max()) for t in anonimas]
    posiciones = np.array(posiciones)
    cmc = [float(np.mean(posiciones <= k)) for k in range(1, rangos + 1)]
    return cmc, np.array(mejores_inscritos), np.array(mejores_anonimos)


def plantilla_de(cfg, detector, extractor, ruta):
    imagen = cargar_imagen(ruta, cfg.limites)
    return extractor.extraer(detector.procesar(imagen).cara)


def galeria_real(cfg, detector, extractor, dataset: Path, carpeta_pruebas: Path):
    """Dataset/ como BD; fotos_prueba/messi/ como consultas nuevas de Messi."""
    galeria = {}
    for f in sorted(dataset.iterdir()):
        if f.suffix.lower() in EXTENSIONES:
            galeria[nombre_de_fichero(f.name)] = plantilla_de(cfg, detector, extractor, f)
    if PERSONA_EQUIPO not in galeria:
        raise SystemExit(f"Falta {PERSONA_EQUIPO} en {dataset}")
    nombres = list(galeria)
    m = np.stack([galeria[n] for n in nombres])
    consultas, descartadas = [], []
    for f in sorted(carpeta_pruebas.iterdir()) if carpeta_pruebas.is_dir() else []:
        if f.suffix.lower() not in EXTENSIONES:
            continue
        try:
            t = plantilla_de(cfg, detector, extractor, f)
        except ErrorBiometrico as e:
            descartadas.append({"foto": f.name, "error": e.codigo})
            continue
        s = np.clip(m @ t, 0.0, None)
        orden = np.argsort(-s)
        propia = float(s[nombres.index(PERSONA_EQUIPO)])
        otros = [(float(s[j]), nombres[j]) for j in orden if nombres[j] != PERSONA_EQUIPO]
        consultas.append({
            "foto": f.name, "puntuacion": round(propia, 4),
            "rango": int(np.where(np.array(nombres)[orden] == PERSONA_EQUIPO)[0][0]) + 1,
            "verificado": propia >= cfg.umbrales.verificacion,
            "identificado": nombres[orden[0]] == PERSONA_EQUIPO
            and s[orden[0]] >= cfg.umbrales.identificacion
            and s[orden[0]] - otros[0][0] >= cfg.defensa.margen_identificacion,
            "mejor_otro": {"id": otros[0][1], "puntuacion": round(otros[0][0], 4)},
        })
    # Separación dentro de la galería: Messi frente a los 49
    mi = galeria[PERSONA_EQUIPO]
    contra_otros = sorted(((float(max(0.0, mi @ galeria[n])), n) for n in nombres if n != PERSONA_EQUIPO),
                          reverse=True)
    return galeria, consultas, descartadas, contra_otros


# --------------------------------------------------------------------------- #
# Figuras
# --------------------------------------------------------------------------- #
def figuras(salida: Path, r: dict, gen, imp, cmc, ins, anon, cfg, consultas):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"figure.dpi": 130, "font.size": 10, "axes.grid": True, "grid.alpha": 0.3})
    uv, ui = cfg.umbrales.verificacion, cfg.umbrales.identificacion
    pct = FuncFormatter(lambda v, _: f"{v:g} %")

    # 1. Distribuciones
    fig, ax = plt.subplots(figsize=(7, 4))
    bins = np.linspace(0, 1, 81)
    ax.hist(imp, bins=bins, density=True, alpha=0.6, label=f"impostores (n={len(imp):,})".replace(",", "."))
    ax.hist(gen, bins=bins, density=True, alpha=0.6, label=f"genuinos (n={len(gen)})")
    ax.axvline(uv, color="k", ls="--", lw=1, label=f"umbral verificación {uv}")
    ax.axvline(ui, color="k", ls=":", lw=1, label=f"umbral identificación {ui}")
    ax.set(xlabel="similitud (coseno)", ylabel="densidad", yscale="log",
           title="Distribución de similitudes (LFW, mitad de test)")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(salida / "01_distribuciones.png"); plt.close(fig)

    # 2. DET (FRR frente a FAR, ejes log)
    u, far, frr = curva(gen, imp, 1000)
    fig, ax = plt.subplots(figsize=(5.5, 5))
    # Un 0 no cabe en escala log: se dibuja en el borde del eje (FAR 0,001 %, FRR 0,01 %),
    # que con estos datos ya es "por debajo de lo que se puede medir"
    ax.plot(np.maximum(far, 1e-5) * 100, np.maximum(frr, 1e-4) * 100, lw=2, label="ArcFace (test)")
    for umbral, marca, nombre in ((uv, "o", "verificación"), (ui, "s", "identificación")):
        fa, fr = far_frr(gen, imp, umbral)
        ax.plot(max(fa, 1e-5) * 100, max(fr, 1e-4) * 100, marca, ms=8,
                label=f"{nombre} {umbral}: FAR {fa:.4%} · FRR {fr:.2%}")
    e = r["lfw"]["test"]["eer"]
    ax.plot([1e-3, 100], [1e-3, 100], color="grey", lw=0.8, ls="--")
    ax.set(xscale="log", yscale="log", xlim=(1e-3, 100), ylim=(1e-2, 100),
           xlabel="FAR (%)", ylabel="FRR (%)", title=f"Curva DET (EER {e:.2%})")
    ax.xaxis.set_major_formatter(pct); ax.yaxis.set_major_formatter(pct)
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout(); fig.savefig(salida / "02_det.png"); plt.close(fig)

    # 3. ROC (TAR frente a FAR, eje X log)
    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.plot(np.maximum(far, 1e-6) * 100, (1 - frr) * 100, lw=2)
    ax.set(xscale="log", xlim=(1e-3, 100), ylim=(90, 100.5), xlabel="FAR (%)", ylabel="TAR = 1 − FRR (%)",
           title="Curva ROC (LFW, mitad de test)")
    ax.xaxis.set_major_formatter(pct)
    fig.tight_layout(); fig.savefig(salida / "03_roc.png"); plt.close(fig)

    # 4. CMC
    fig, ax = plt.subplots(figsize=(5.5, 4))
    k = np.arange(1, len(cmc) + 1)
    ax.plot(k, np.array(cmc) * 100, "o-", ms=4)
    ax.set(xlabel="rango k", ylabel="identificación correcta en top-k (%)", xticks=k[::2],
           ylim=(min(cmc) * 100 - 1, 100.3),
           title=f"Curva CMC (rank-1 {cmc[0]:.2%})\n{r['lfw']['test']['identificacion']['personas']} personas + "
                 f"{r['lfw']['test']['identificacion']['distractores']} distractores en la galería")
    fig.tight_layout(); fig.savefig(salida / "04_cmc.png"); plt.close(fig)

    # 5. Identificación abierta: FPIR / FNIR
    fig, ax = plt.subplots(figsize=(6, 4))
    uu = np.linspace(0, 1, 400)
    fpir = [np.mean(anon >= x) * 100 for x in uu]
    fnir = [np.mean(ins < x) * 100 for x in uu]
    ax.plot(uu, fpir, label="FPIR: desconocido identificado como alguien")
    ax.plot(uu, fnir, label="FNIR: inscrito no identificado")
    ax.axvline(ui, color="k", ls=":", lw=1, label=f"umbral identificación {ui}")
    ax.set(xlabel="umbral", ylabel="%", title="Identificación abierta (LFW, mitad de test)", ylim=(-1, 40))
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(salida / "05_fpir_fnir.png"); plt.close(fig)

    # 6. Messi: fotos nuevas
    if consultas:
        fig, ax = plt.subplots(figsize=(7, 4.2))
        orden = sorted(consultas, key=lambda c: c["puntuacion"])
        y = np.arange(len(orden))
        ax.barh(y, [c["puntuacion"] for c in orden],
                color=["tab:green" if c["verificado"] else "tab:red" for c in orden], label="contra Messi")
        ax.plot([c["mejor_otro"]["puntuacion"] for c in orden], y, "kx", label="mejor de los otros 49")
        ax.axvline(uv, color="k", ls="--", lw=1, label=f"umbral {uv}")
        ax.set_yticks(y, [c["foto"].rsplit(".", 1)[0][:34] for c in orden], fontsize=6.5)
        ax.set(xlim=(0, 1), xlabel="similitud",
               title=f"{PERSONA_EQUIPO}: {sum(c['verificado'] for c in orden)}/{len(orden)} fotos nuevas verificadas")
        ax.legend(fontsize=7, loc="lower right")
        fig.tight_layout(); fig.savefig(salida / "06_messi_fotos_nuevas.png"); plt.close(fig)


def _a_json(o):
    """Tipos de numpy (int64, float32, bool_) a tipos de Python para json."""
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"{type(o).__name__} no se puede pasar a JSON")


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=None)
    p.add_argument("--personas", type=int, default=300, help="personas de LFW con 2 o más fotos (se reparten en dos mitades)")
    p.add_argument("--fotos", type=int, default=4)
    p.add_argument("--desconocidos", type=int, default=400)
    p.add_argument("--semilla", type=int, default=11)
    p.add_argument("--dataset", type=Path, default=RAIZ / "Dataset")
    p.add_argument("--pruebas", type=Path, default=RAIZ / "fotos_prueba" / "messi")
    p.add_argument("--salida", type=Path, default=RAIZ / "docs" / "graficas")
    a = p.parse_args()

    cfg = cargar_config(a.config)
    a.salida.mkdir(parents=True, exist_ok=True)

    # 1. LFW: calibración y test con personas distintas
    extractor, conocidas, anonimas, descartadas = templates_lfw(cfg, a.personas, a.fotos, a.desconocidos, a.semilla)
    nombres = sorted(conocidas)
    random.Random(a.semilla).shuffle(nombres)
    mitad = len(nombres) // 2
    cal = {n: conocidas[n] for n in nombres[:mitad]}
    test = {n: conocidas[n] for n in nombres[mitad:]}
    anon_cal, anon_test = anonimas[: len(anonimas) // 2], anonimas[len(anonimas) // 2:]

    g_cal, i_cal = pares(extractor, cal)
    g_test, i_test = pares(extractor, test)
    eer_cal, u_eer = eer(g_cal, i_cal)
    u_far = umbral_para_far(i_cal, 1e-3)
    eer_test, u_eer_test = eer(g_test, i_test)
    # los desconocidos de calibración (y las personas de calibración) hacen de distractores
    distractores = list(anon_cal) + [t for ts in cal.values() for t in ts[:1]]
    cmc, ins, anon = identificacion(test, anon_test, distractores)

    def punto(umbral):
        fa, fr = far_frr(g_test, i_test, umbral)
        return {"umbral": round(float(umbral), 4), "far": fa, "frr": fr}

    ui = cfg.umbrales.identificacion
    r = {
        "config": {"verificacion": cfg.umbrales.verificacion, "identificacion": ui,
                   "margen": cfg.defensa.margen_identificacion},
        "lfw": {
            "semilla": a.semilla, "descartadas": descartadas,
            "calibracion": {"personas": len(cal), "genuinos": len(g_cal), "impostores": len(i_cal),
                            "eer": eer_cal, "umbral_eer": u_eer, "umbral_far_0.1%": u_far},
            "test": {
                "personas": len(test), "genuinos": len(g_test), "impostores": len(i_test),
                "eer": eer_test, "umbral_eer": u_eer_test,
                "con_umbral_eer_de_calibracion": punto(u_eer),
                "con_umbral_far_0.1%_de_calibracion": punto(u_far),
                "con_umbral_verificacion_config": punto(cfg.umbrales.verificacion),
                "con_umbral_identificacion_config": punto(ui),
                "identificacion": {
                    "personas": len(test), "distractores": len(distractores), "consultas": len(ins),
                    "rank1": cmc[0], "rank5": cmc[4], "rank10": cmc[9],
                    "fnir": float(np.mean(ins < ui)), "desconocidos": len(anon),
                    "fpir": float(np.mean(anon >= ui)) if len(anon) else 0.0,
                },
            },
        },
    }

    # 2. Galería real + Messi con fotos nuevas
    cfg_g = replace(cfg, detector=replace(cfg.detector, varias_caras="mayor"))
    detector = Detector(cfg_g)
    _, consultas, desc_m, contra_otros = galeria_real(cfg_g, detector, extractor, a.dataset, a.pruebas)
    r["messi"] = {
        "fotos_nuevas": len(consultas) + len(desc_m), "procesadas": len(consultas),
        "descartadas": desc_m,
        "verificadas": sum(c["verificado"] for c in consultas),
        "identificadas": sum(c["identificado"] for c in consultas),
        "rank1": sum(c["rango"] == 1 for c in consultas),
        "confundido_con_otro": sum(c["mejor_otro"]["puntuacion"] >= ui
                                   and c["mejor_otro"]["puntuacion"] > c["puntuacion"] for c in consultas),
        "puntuacion_media": float(np.mean([c["puntuacion"] for c in consultas])) if consultas else None,
        "puntuacion_min": float(min(c["puntuacion"] for c in consultas)) if consultas else None,
        "max_contra_otros_inscritos": [{"id": n, "puntuacion": round(s, 4)} for s, n in contra_otros[:3]],
        "consultas": consultas,
    }

    figuras(a.salida, r, g_test, i_test, cmc, ins, anon, cfg, consultas)
    (a.salida / "resultados.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=_a_json), encoding="utf-8")

    t = r["lfw"]["test"]
    print(f"\nCalibración: {len(cal)} personas · EER {eer_cal:.2%} (umbral {u_eer:.3f}) · umbral FAR 0,1 % = {u_far:.3f}")
    print(f"Test:        {len(test)} personas · {len(g_test)} genuinos · {len(i_test)} impostores · EER {eer_test:.2%}")
    for k in ("con_umbral_eer_de_calibracion", "con_umbral_far_0.1%_de_calibracion",
              "con_umbral_verificacion_config", "con_umbral_identificacion_config"):
        print(f"  {k:<38} umbral {t[k]['umbral']:.3f}  FAR {t[k]['far']:.4%}  FRR {t[k]['frr']:.2%}")
    i = t["identificacion"]
    print(f"  identificación: rank-1 {i['rank1']:.2%} · rank-5 {i['rank5']:.2%} · FNIR {i['fnir']:.2%} · "
          f"FPIR {i['fpir']:.2%} ({i['desconocidos']} desconocidos)")
    m = r["messi"]
    print(f"\n{PERSONA_EQUIPO}: {m['verificadas']}/{m['procesadas']} fotos nuevas verificadas · "
          f"{m['identificadas']} identificadas · rank-1 {m['rank1']} · {len(m['descartadas'])} descartadas")
    print(f"Figuras y resultados en {a.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
