"""Prepara un conjunto de prueba con N personas de LFW (Labeled Faces in the Wild).

Mientras el docente no entregue las 50 fotos comunes, sirve para probar el
detector con caras de personas conocidas en condiciones reales (luz, pose,
gente al fondo...).

Uso:
    python scripts/preparar_lfw.py                  # 50 personas en data/lfw
    python scripts/preparar_lfw.py --personas 100 --semilla 7
    python scripts/probar_detector.py data/lfw/fotos --anotadas --salida caras_lfw

Genera:
    data/lfw/fotos/<Nombre>.jpg   una foto por persona (la primera de LFW)
    data/lfw/personas.csv         id,foto  (formato pensado para la inscripción)

El .tgz se guarda en ~/.cache/jumex (fuera del repo y de OneDrive) y se
verifica su SHA256. Solo usa la librería estándar.
"""

import argparse
import csv
import hashlib
import random
import sys
import tarfile
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
URL = "https://ndownloader.figshare.com/files/5976018"
# Mismo hash que publica scikit-learn (sklearn/datasets/_lfw.py)
SHA256 = "055f7d9c632d7370e6fb4afc7468d40f970c34a80d4c6f50ffec63f5a8d536c0"
CACHE = Path.home() / ".cache" / "jumex" / "lfw.tgz"


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def obtener_archivo() -> Path:
    if CACHE.is_file() and sha256(CACHE) == SHA256:
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Descargando LFW (~180 MB) en {CACHE} ...")
    temporal = CACHE.with_suffix(".part")
    urllib.request.urlretrieve(URL, temporal)
    if sha256(temporal) != SHA256:
        temporal.unlink()
        raise RuntimeError("El SHA256 de lfw.tgz no coincide: descarga corrupta o manipulada")
    temporal.replace(CACHE)
    return CACHE


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrae N personas de LFW para probar el detector")
    parser.add_argument("--personas", type=int, default=50)
    parser.add_argument("--semilla", type=int, default=42, help="misma semilla = mismas personas")
    parser.add_argument("--salida", type=Path, default=RAIZ / "data" / "lfw")
    args = parser.parse_args()

    try:
        archivo = obtener_archivo()
    except (OSError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    with tarfile.open(archivo, "r:gz") as tar:
        # lfw/<Nombre>/<Nombre>_0001.jpg -> la primera foto de cada persona
        primeras = {m.name.split("/")[1]: m for m in tar.getmembers()
                    if m.isfile() and m.name.endswith("_0001.jpg")}
        nombres = sorted(primeras)
        if args.personas > len(nombres):
            print(f"LFW solo tiene {len(nombres)} personas", file=sys.stderr)
            return 1
        elegidos = sorted(random.Random(args.semilla).sample(nombres, args.personas))

        carpeta_fotos = args.salida / "fotos"
        carpeta_fotos.mkdir(parents=True, exist_ok=True)
        filas = []
        for nombre in elegidos:
            destino = carpeta_fotos / f"{nombre}.jpg"
            destino.write_bytes(tar.extractfile(primeras[nombre]).read())
            filas.append({"id": nombre.replace("_", " "), "foto": str(destino.relative_to(RAIZ))})

    with (args.salida / "personas.csv").open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=["id", "foto"])
        escritor.writeheader()
        escritor.writerows(filas)

    print(f"{len(filas)} personas en {carpeta_fotos}  ·  lista en {args.salida / 'personas.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
