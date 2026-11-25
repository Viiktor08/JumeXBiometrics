"""Mide el coste y el efecto de las defensas que dependen de un umbral (THREAT_MODEL.md).

Uso:
    python scripts/medir_defensas.py                 # 150 personas de LFW
    python scripts/medir_defensas.py --personas 300

Usa el mismo lfw.tgz que scripts/evaluar.py (~/.cache/jumex, verificado por SHA256)
y no toca ninguna base de datos. Mide:

1. Paso 4, margen de identificación: la primera foto de cada persona hace de BD;
   el resto se identifican. Para cada margen, cuántas identificaciones CORRECTAS
   se pierden (pasan a "ambiguo") y cuántas INCORRECTAS se evitan. Además, cuántos
   desconocidos (personas fuera de la BD) dejan de colarse.
2. Paso 5, cara en _sd: cuántos recortes de cara legítimos se aceptan (cara
   alineada 112x112 y recorte holgado de la caja) y cuántas imágenes que NO son
   una cara se rechazan (ruido, colores lisos, degradados y trozos de fondo de
   las propias fotos de LFW).
"""

import argparse
import random
import sys
import tarfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biometria.config import cargar_config  # noqa: E402
from biometria.detector import Detector, iou  # noqa: E402
from biometria.errores import ErrorBiometrico, SinCara  # noqa: E402
from biometria.extractores import crear_extractor  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402
from preparar_lfw import obtener_archivo  # noqa: E402

MARGENES = (0.0, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15)


def cargar(cfg, personas: int, fotos: int, desconocidos: int, semilla: int):
    """Templates de LFW + recortes de cara y de fondo para el paso 5."""
    cfg = replace(cfg, detector=replace(cfg.detector, varias_caras="mayor"))
    detector = Detector(cfg)
    extractor = crear_extractor(cfg)
    azar = random.Random(semilla)
    conocidas: dict[str, list[np.ndarray]] = {}
    anonimas: list[np.ndarray] = []
    caras_ok: list[np.ndarray] = []      # recortes legítimos para _sd
    no_caras: list[np.ndarray] = []      # trozos de fondo (sin cara)

    with tarfile.open(obtener_archivo(), "r:gz") as tar:
        por_persona = defaultdict(list)
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith(".jpg"):
                por_persona[m.name.split("/")[1]].append(m)
        varias = sorted(p for p, fs in por_persona.items() if len(fs) >= 2)
        una = sorted(p for p, fs in por_persona.items() if len(fs) == 1)
        elegidas = azar.sample(varias, min(personas, len(varias)))
        extras = azar.sample(una, min(desconocidos, len(una)))

        def procesar(miembro, guardar_recortes: bool):
            try:
                imagen = cargar_imagen(tar.extractfile(miembro).read(), cfg.limites)
                detecciones = detector.detectar(imagen)
                procesada = detector.procesar_deteccion(imagen, detector.elegir_cara(detecciones))
            except ErrorBiometrico:
                return None
            if guardar_recortes:
                caras_ok.append(procesada.cara)
                x, y, w, h = procesada.deteccion.caja
                m = int(0.15 * max(w, h))
                recorte = imagen[max(0, y - m):y + h + m, max(0, x - m):x + w + m]
                if recorte.size:
                    caras_ok.append(recorte)
                # Esquinas de 64x64 que no tocan ninguna cara detectada: fondo
                alto, ancho = imagen.shape[:2]
                for (fx, fy) in ((0, 0), (ancho - 64, 0), (0, alto - 64), (ancho - 64, alto - 64)):
                    if all(iou((fx, fy, 64, 64), d.caja) == 0 for d in detecciones):
                        no_caras.append(imagen[fy:fy + 64, fx:fx + 64])
            return extractor.extraer(procesada.cara)

        for i, persona in enumerate(elegidas, start=1):
            print(f"\r  extrayendo personas {i}/{len(elegidas)}", end="", flush=True)
            miembros = sorted(por_persona[persona], key=lambda m: m.name)[:fotos]
            ts = [t for t in (procesar(m, j == 0) for j, m in enumerate(miembros)) if t is not None]
            if len(ts) >= 2:
                conocidas[persona] = ts
        print()
        for i, persona in enumerate(extras, start=1):
            print(f"\r  extrayendo desconocidos {i}/{len(extras)}", end="", flush=True)
            t = procesar(por_persona[persona][0], False)
            if t is not None:
                anonimas.append(t)
        print()
    return detector, extractor, conocidas, anonimas, caras_ok, no_caras


def medir_margen(extractor, conocidas, anonimas, umbral: float) -> None:
    galeria = {p: ts[0] for p, ts in conocidas.items()}
    nombres = list(galeria)
    matriz = np.stack([galeria[p] for p in nombres])

    def top2(t):
        s = np.maximum(matriz @ t / (np.linalg.norm(matriz, axis=1) * np.linalg.norm(t)), 0.0)
        orden = np.argsort(-s)
        return nombres[orden[0]], float(s[orden[0]]), float(s[orden[1]])

    sondas = [(p, top2(t)) for p, ts in conocidas.items() for t in ts[1:]]
    fuera = [top2(t) for t in anonimas]
    print(f"\nPaso 4 · margen de identificación (umbral {umbral}, BD de {len(nombres)} personas, "
          f"{len(sondas)} fotos de inscritos, {len(fuera)} desconocidos)")
    print("  margen | aciertos perdidos | errores evitados | desconocidos que se colaban")
    for margen in MARGENES:
        perdidos = evitados = colados = 0
        for real, (mejor, s1, s2) in sondas:
            if s1 >= umbral and s1 - s2 < margen:
                perdidos += mejor == real
                evitados += mejor != real
        base_colados = sum(s1 >= umbral for _, s1, _ in fuera)
        colados = sum(s1 >= umbral and s1 - s2 >= margen for _, s1, s2 in fuera)
        print(f"  {margen:5.2f}  | {perdidos:4d} ({100 * perdidos / len(sondas):5.2f} %)   | "
              f"{evitados:4d}             | {colados}/{base_colados}")


def medir_cara_sd(detector, caras_ok, no_caras, semilla: int) -> None:
    azar = np.random.default_rng(semilla)
    sinteticas = []
    for _ in range(200):
        sinteticas.append(azar.integers(0, 256, (112, 112, 3), dtype=np.uint8))          # ruido
        sinteticas.append(np.full((112, 112, 3), azar.integers(0, 256, 3), dtype=np.uint8))  # liso
        g = np.linspace(0, 255, 112, dtype=np.uint8)
        sinteticas.append(np.dstack([np.tile(g, (112, 1))] * 3))                           # degradado

    def aceptadas(imagenes):
        n = 0
        for im in imagenes:
            try:
                detector.comprobar_cara_recortada(im)
                n += 1
            except SinCara:
                pass
        return n

    ok = aceptadas(caras_ok)
    fondo = aceptadas(no_caras)
    sint = aceptadas(sinteticas)
    print("\nPaso 5 · ¿hay una cara en la imagen de _sd?")
    print(f"  caras legítimas aceptadas:     {ok}/{len(caras_ok)} ({100 * ok / len(caras_ok):.1f} %)")
    print(f"  trozos de fondo rechazados:    {len(no_caras) - fondo}/{len(no_caras)}")
    print(f"  ruido/liso/degradado rechaz.:  {len(sinteticas) - sint}/{len(sinteticas)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--personas", type=int, default=150)
    parser.add_argument("--fotos", type=int, default=4)
    parser.add_argument("--desconocidos", type=int, default=300)
    parser.add_argument("--semilla", type=int, default=7)
    args = parser.parse_args()
    cfg = cargar_config(args.config)
    detector, extractor, conocidas, anonimas, caras_ok, no_caras = cargar(
        cfg, args.personas, args.fotos, args.desconocidos, args.semilla)
    medir_margen(extractor, conocidas, anonimas, cfg.umbrales.identificacion)
    medir_cara_sd(detector, caras_ok, no_caras, args.semilla)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
