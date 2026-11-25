"""Compara las caras de dos o más fotos con el extractor de config.yaml.

Uso:
    python scripts/comparar_caras.py foto1.jpg foto2.jpg
    python scripts/comparar_caras.py data/fotos/*.jpg        # matriz de todas contra todas

Pasa cada foto por el pipeline completo (detector -> calidad -> alineado ->
extractor) y muestra la similitud en [0, 1] (1 = idénticas) entre cada par.
Sirve para probar el extractor a mano antes de tener la base de datos.

Con ArcFace (config.yaml), la misma persona suele dar 0.4–0.9 y personas
distintas cerca de 0 (umbral de verificación 0.35; ver "Cuánto acierta" en el README).
"""

import argparse
import sys
from pathlib import Path

# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biometria.config import cargar_config  # noqa: E402
from biometria.detector import Detector  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.extractores import crear_extractor  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Similitud entre las caras de varias fotos")
    parser.add_argument("fotos", type=Path, nargs="+", help="dos o más fotos")
    parser.add_argument("--config", default=None, help="fichero de configuración alternativo")
    args = parser.parse_args()
    if len(args.fotos) < 2:
        parser.error("hacen falta al menos dos fotos")

    try:
        cfg = cargar_config(args.config)
        detector = Detector(cfg)
        extractor = crear_extractor(cfg)
    except ErrorBiometrico as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Extractor: {extractor}")

    templates = {}
    for ruta in args.fotos:
        try:
            cara = detector.procesar(cargar_imagen(ruta, cfg.limites)).cara
            # Nombre corto si no se repite; si no, la ruta (a/image.png vs b/image.png)
            nombre = ruta.stem if sum(r.stem == ruta.stem for r in args.fotos) == 1 else str(ruta)
            templates[nombre] = extractor.extraer(cara)
        except ErrorBiometrico as e:
            print(f"  descartada {ruta}: [{e.codigo}] {e.mensaje}")
    if len(templates) < 2:
        print("No quedan al menos dos fotos válidas para comparar", file=sys.stderr)
        return 1

    nombres = list(templates)
    umbral = cfg.umbrales.verificacion
    if len(nombres) == 2:
        a, b = nombres
        puntuacion = extractor.comparar(templates[a], templates[b])
        decision = "MISMA persona" if puntuacion >= umbral else "personas DISTINTAS"
        print(f"\n{a}  vs  {b}:  {puntuacion:.4f}  -> {decision} (umbral {umbral})")
        return 0

    ancho = max(len(n) for n in nombres)
    print("\n" + " " * ancho + "  " + "  ".join(f"{i:>6}" for i in range(len(nombres))))
    for i, a in enumerate(nombres):
        fila = "  ".join(f"{extractor.comparar(templates[a], templates[b]):6.3f}" for b in nombres)
        print(f"{a:>{ancho}}  {fila}   [{i}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
