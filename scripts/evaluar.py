"""Evalúa el extractor de la configuración con fotos de LFW y propone umbrales.

Uso:
    python scripts/evaluar.py                              # config.yaml, 150 personas
    python scripts/evaluar.py --config config_lens.yaml --personas 300 --fotos 5

Toma personas de LFW con al menos 2 fotos (hasta --fotos por persona) y las pasa
por el pipeline completo (detector ► calidad ► alineado ► extractor). Después:

- Verificación (1:1): compara todos los pares. Pares GENUINOS = misma persona,
  pares IMPOSTORES = personas distintas. Con ellos calcula, para cada umbral:
      FAR (False Accept Rate)  = impostores aceptados / impostores
      FRR (False Reject Rate)  = genuinos rechazados / genuinos
  el EER (punto en que FAR = FRR) y el umbral para un FAR objetivo.
- Identificación (1:N): la primera foto de cada persona hace de BD y el resto
  de fotos se identifican contra todas (rank-1 = el más parecido es el bueno).
  Además, personas con una sola foto en LFW (que no están en la BD) hacen de
  desconocidos: se mide cuántas veces el sistema las confunde con alguien.

Usa el lfw.tgz de scripts/preparar_lfw.py (~/.cache/jumex, verificado por SHA256).
"""

import argparse
import random
import sys
import tarfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biometria.config import cargar_config  # noqa: E402
from biometria.detector import Detector  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.extractores import crear_extractor  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402
from preparar_lfw import obtener_archivo  # noqa: E402

FAR_OBJETIVO = (1e-2, 1e-3, 1e-4)
# Umbrales de la tabla FAR/FRR (además de los de la config)
TABLA_UMBRALES = np.round(np.arange(0.05, 1.0, 0.05), 2)


def templates_lfw(cfg, personas: int, fotos: int, desconocidos: int, semilla: int):
    """Extrae los templates: ({persona: [templates]}, [templates de desconocidos], descartadas)."""
    # En LFW suele haber gente al fondo: se usa la cara más grande, como en la web
    cfg = replace(cfg, detector=replace(cfg.detector, varias_caras="mayor"))
    detector = Detector(cfg)
    extractor = crear_extractor(cfg)
    azar = random.Random(semilla)

    with tarfile.open(obtener_archivo(), "r:gz") as tar:
        por_persona = defaultdict(list)
        for m in tar.getmembers():
            if m.isfile() and m.name.endswith(".jpg"):
                por_persona[m.name.split("/")[1]].append(m)
        varias = sorted(p for p, fs in por_persona.items() if len(fs) >= 2)
        una = sorted(p for p, fs in por_persona.items() if len(fs) == 1)
        elegidas = azar.sample(varias, min(personas, len(varias)))
        extras = azar.sample(una, min(desconocidos, len(una)))

        descartadas = 0

        def extraer(miembro):
            nonlocal descartadas
            try:
                imagen = cargar_imagen(tar.extractfile(miembro).read(), cfg.limites)
                return extractor.extraer(detector.procesar(imagen).cara)
            except ErrorBiometrico:
                descartadas += 1
                return None

        conocidas = {}
        for i, persona in enumerate(elegidas, start=1):
            print(f"\r  extrayendo personas {i}/{len(elegidas)}", end="", flush=True)
            miembros = sorted(por_persona[persona], key=lambda m: m.name)[:fotos]
            templates = [t for t in map(extraer, miembros) if t is not None]
            if len(templates) >= 2:
                conocidas[persona] = templates
        print()
        anonimas = []
        for i, persona in enumerate(extras, start=1):
            print(f"\r  extrayendo desconocidos {i}/{len(extras)}", end="", flush=True)
            t = extraer(por_persona[persona][0])
            if t is not None:
                anonimas.append(t)
        print()
    return extractor, conocidas, anonimas, descartadas


def puntuaciones(extractor, conocidas):
    """Puntuaciones de los pares genuinos e impostores (una foto contra otra)."""
    etiquetas, templates = [], []
    for persona, ts in conocidas.items():
        etiquetas += [persona] * len(ts)
        templates += ts
    genuinas, impostoras = [], []
    for i in range(len(templates)):
        for j in range(i + 1, len(templates)):
            s = extractor.comparar(templates[i], templates[j])
            (genuinas if etiquetas[i] == etiquetas[j] else impostoras).append(s)
    return np.array(genuinas), np.array(impostoras)


def far_frr(genuinas, impostoras, umbral):
    return float(np.mean(impostoras >= umbral)), float(np.mean(genuinas < umbral))


def eer(genuinas, impostoras):
    """(EER, umbral) buscando el umbral donde FAR y FRR se cruzan."""
    candidatos = np.unique(np.concatenate([genuinas, impostoras]))
    mejor = min(candidatos, key=lambda u: abs(np.subtract(*far_frr(genuinas, impostoras, u))))
    far, frr = far_frr(genuinas, impostoras, mejor)
    return (far + frr) / 2, float(mejor)


def umbral_para_far(impostoras, far):
    """Umbral más bajo con FAR <= far."""
    orden = np.sort(impostoras)[::-1]
    k = int(np.floor(far * len(orden)))
    return float(orden[k]) + 1e-6 if k < len(orden) else 0.0


def resumen(nombre, x):
    return (f"{nombre:<11} n={len(x):>7}  media {x.mean():.3f}  "
            f"p1 {np.percentile(x, 1):.3f}  mediana {np.median(x):.3f}  p99 {np.percentile(x, 99):.3f}  "
            f"máx {x.max():.3f}")


def identificacion(extractor, conocidas, anonimas, umbral):
    """Rank-1 con la primera foto de cada persona como BD; desconocidos aceptados por error."""
    nombres = list(conocidas)
    bd = [conocidas[p][0] for p in nombres]

    def ranking(t):
        return sorted(((extractor.comparar(t, g), p) for g, p in zip(bd, nombres)), reverse=True)

    aciertos = total = aceptados_bien = 0
    segundos = []
    for persona in nombres:
        for t in conocidas[persona][1:]:
            r = ranking(t)
            total += 1
            aciertos += r[0][1] == persona
            aceptados_bien += r[0][1] == persona and r[0][0] >= umbral
            segundos.append(r[1][0])
    falsos = [ranking(t)[0][0] for t in anonimas]
    return {
        "personas": len(nombres), "consultas": total,
        "rank1": aciertos / total if total else 0.0,
        "identificados": aceptados_bien / total if total else 0.0,
        "desconocidos": len(falsos),
        "desconocidos_aceptados": float(np.mean(np.array(falsos) >= umbral)) if falsos else 0.0,
        "segundo_p99": float(np.percentile(segundos, 99)) if segundos else 0.0,
        "desconocido_p99": float(np.percentile(falsos, 99)) if falsos else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="fichero de configuración alternativo")
    parser.add_argument("--personas", type=int, default=150, help="personas con 2 o más fotos")
    parser.add_argument("--fotos", type=int, default=4, help="fotos como mucho por persona")
    parser.add_argument("--desconocidos", type=int, default=300, help="personas fuera de la BD (1:N)")
    parser.add_argument("--semilla", type=int, default=7)
    parser.add_argument("--tabla", action="store_true", help="tabla FAR/FRR para umbrales de 0.05 a 0.95")
    args = parser.parse_args()

    try:
        cfg = cargar_config(args.config)
        extractor, conocidas, anonimas, descartadas = templates_lfw(
            cfg, args.personas, args.fotos, args.desconocidos, args.semilla)
    except (ErrorBiometrico, OSError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    genuinas, impostoras = puntuaciones(extractor, conocidas)
    if len(genuinas) == 0 or len(impostoras) == 0:
        print("No hay suficientes pares para evaluar", file=sys.stderr)
        return 1

    print(f"\nExtractor {extractor!r}")
    print(f"{len(conocidas)} personas · {sum(map(len, conocidas.values()))} fotos útiles · "
          f"{descartadas} descartadas por el detector o la calidad\n")
    print(resumen("Genuinos", genuinas))
    print(resumen("Impostores", impostoras))

    valor_eer, umbral_eer = eer(genuinas, impostoras)
    print(f"\nEER {valor_eer:.2%} con umbral {umbral_eer:.3f}")
    print("\n  FAR objetivo   umbral   FAR real   FRR")
    for objetivo in FAR_OBJETIVO:
        u = umbral_para_far(impostoras, objetivo)
        far, frr = far_frr(genuinas, impostoras, u)
        print(f"  {objetivo:>12.2%}   {u:.3f}    {far:.3%}   {frr:.2%}")

    if args.tabla:
        print("\n  umbral   FAR        FRR")
        for u in TABLA_UMBRALES:
            far, frr = far_frr(genuinas, impostoras, u)
            print(f"  {u:.2f}     {far:.4%}   {frr:.2%}")

    for nombre, umbral in (("verificación", cfg.umbrales.verificacion),
                           ("identificación", cfg.umbrales.identificacion)):
        far, frr = far_frr(genuinas, impostoras, umbral)
        print(f"\nUmbral de {nombre} de la config ({umbral:.3f}): FAR {far:.3%} · FRR {frr:.2%}")

    r = identificacion(extractor, conocidas, anonimas, cfg.umbrales.identificacion)
    print(f"\nIdentificación 1:N contra {r['personas']} personas ({r['consultas']} consultas)")
    print(f"  rank-1 (el más parecido es el bueno):         {r['rank1']:.2%}")
    print(f"  identificados (rank-1 y supera el umbral):    {r['identificados']:.2%}")
    print(f"  desconocidos confundidos con alguien:         {r['desconocidos_aceptados']:.2%} "
          f"de {r['desconocidos']}")
    print(f"  p99 del 2.º candidato de un inscrito:         {r['segundo_p99']:.3f}")
    print(f"  p99 del mejor candidato de un desconocido:    {r['desconocido_p99']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
