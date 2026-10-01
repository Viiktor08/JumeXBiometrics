"""Inscribe en bloque a las personas de un CSV con columnas id,foto.

Uso:
    python scripts/inscribir_csv.py data/lfw/personas.csv --config config_lens.yaml
    python scripts/inscribir_csv.py fotos_profesor.csv                 # config.yaml

Las rutas de `foto` relativas se resuelven respecto a la raíz del repositorio.
Guarda además el retrato de cada persona para la web (data/.../caras/).
Las filas que fallan (sin cara, borrosa...) se informan y se saltan.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(RAIZ))

from biometria.config import cargar_config  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.sistema import SistemaBiometrico  # noqa: E402
from biometria.web.analisis import Galeria, analizar  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Inscripción masiva desde un CSV id,foto")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--config", default=None, help="fichero de configuración (por defecto config.yaml)")
    args = parser.parse_args()

    try:
        cfg = cargar_config(args.config)
        sistema = SistemaBiometrico(cfg)
    except ErrorBiometrico as e:
        print(f"Error [{e.codigo}]: {e.mensaje}", file=sys.stderr)
        return 1
    galeria = Galeria(cfg.bd.ruta.parent / "caras")

    with args.csv.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    if not filas or not {"id", "foto"} <= set(filas[0]):
        print("El CSV debe tener las columnas id,foto", file=sys.stderr)
        return 1

    resultados = Counter()
    for fila in filas:
        ruta = Path(fila["foto"])
        ruta = ruta if ruta.is_absolute() else RAIZ / ruta
        analisis = analizar(sistema, ruta)
        try:
            if analisis.error is not None:
                raise analisis.error
            r = sistema.inscribir_t(fila["id"], analisis.template)
            clave = sistema.bd.muestras_de(r.id)[-1][0]  # la recién añadida va al final
            galeria.guardar_muestra_de_analisis(r.id, clave, analisis)
            resultados["ok"] += 1
            print(f"{'ok':>16}  {r.id} ({r.muestras} muestra/s)")
        except ErrorBiometrico as e:
            resultados[e.codigo] += 1
            print(f"{e.codigo:>16}  {fila['id']}: {e.mensaje}")

    print("\nResumen:", ", ".join(f"{k}={v}" for k, v in resultados.most_common()))
    print(f"BD: {cfg.bd.ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
