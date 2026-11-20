"""Arranque de JumeX Lens en Docker (lo usa docker-compose.yml).

    python scripts/arrancar_web.py [opciones de python -m biometria.web]

1. Si la base de datos de la web (data/lens/bd.csv) todavía no existe, la crea
   inscribiendo todas las fotos de Dataset/ (persona_año.jpg). Al inscribir la
   primera persona se genera la clave maestra ALEATORIA en ~/.jumex/clave_lens.key:
   cada equipo que levanta la web tiene su propia clave, que nunca sale de su
   ordenador ni viaja por ningún sitio.
2. Arranca la web con las opciones recibidas. El PIN de administrador también se
   genera aleatorio en cada arranque (salvo que se fije en BIOMETRIA_ADMIN_PASS).

Si la BD ya existe no se toca: se reutiliza tal cual en cada arranque.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from biometria.config import cargar_config  # noqa: E402

CONFIG = RAIZ / "config_lens.yaml"
DATASET = RAIZ / "Dataset"
EXTENSIONES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def hay_fotos(carpeta: Path) -> bool:
    return carpeta.is_dir() and any(
        f.suffix.lower() in EXTENSIONES and not f.name.startswith(".") for f in carpeta.iterdir())


def preparar_bd() -> int:
    cfg = cargar_config(str(CONFIG))
    bd = cfg.bd.ruta
    if bd.exists():
        print(f"[arranque] BD existente: {bd} (no se modifica)", flush=True)
        return 0
    if not hay_fotos(DATASET):
        print("[arranque] No hay BD ni fotos en Dataset/: la web arranca VACÍA.\n"
              "           Inscribe personas desde la web con el PIN de administrador.", flush=True)
        return 0

    print(f"[arranque] Primera vez: creando la BD con las fotos de Dataset/ "
          f"(clave nueva y aleatoria en {cfg.bd.ruta_clave})...", flush=True)
    csv = bd.parent / "dataset_ids.csv"
    for orden in (
        [sys.executable, str(RAIZ / "scripts" / "preparar_dataset.py"), str(DATASET), "--salida", str(csv)],
        [sys.executable, str(RAIZ / "scripts" / "inscribir_csv.py"), str(csv), "--config", str(CONFIG)],
    ):
        if subprocess.run(orden, cwd=RAIZ).returncode != 0:
            print("[arranque] Falló la inscripción del Dataset (ver arriba).", flush=True)
            return 1
    csv.unlink(missing_ok=True)
    return 0


def main() -> int:
    if preparar_bd() != 0:
        return 1
    orden = [sys.executable, "-m", "biometria.web", "--config", str(CONFIG), *sys.argv[1:]]
    os.execv(sys.executable, orden)  # la web pasa a ser el proceso principal (recibe Ctrl+C / docker stop)


if __name__ == "__main__":
    sys.exit(main())
