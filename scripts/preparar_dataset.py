"""Genera el CSV id,foto a partir de una carpeta de fotos con nombre "persona_año.ext".

Uso:
    python scripts/preparar_dataset.py Dataset                       # data/dataset_ids.csv
    python scripts/preparar_dataset.py Dataset --salida otro.csv
    python scripts/inscribir_csv.py data/dataset_ids.csv --config config_lens.yaml

El nombre de la persona sale del nombre del fichero:

    christian_bale_2012.jpg                  ->  Christian Bale
    mary-kate_olsen_2011.jpg                 ->  Mary-Kate Olsen
    elisabeth_II_1945.jpg                    ->  Elisabeth II
    james_phelps_(gemelo_weasley)_2011.jpg   ->  James Phelps

Se quita el año final y lo que vaya entre paréntesis (el validador de ids no
admite paréntesis). El nombre se deja como esté escrito en el fichero: si hay
una errata, se corrige renombrando la foto. Dos fotos con el mismo nombre dan
la misma persona (se inscriben como muestras distintas).

Las fotos del dataset NO van al repositorio (son datos de personas reales).
"""

import argparse
import csv
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(RAIZ))

from biometria.bd import validar_id  # noqa: E402
from biometria.errores import IdInvalido  # noqa: E402

EXTENSIONES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NUMEROS_ROMANOS = {"ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii"}


def nombre_de_fichero(fichero: str) -> str:
    """'christian_bale_2012.jpg' -> 'Christian Bale' (ya validado como id)."""
    base = Path(fichero).stem
    base = re.sub(r"\([^)]*\)", "", base)             # quita "(twin_melody)"
    base = re.sub(r"_\d{4}$", "", base.strip("_ "))   # quita el año final
    palabras = [p.upper() if p.lower() in NUMEROS_ROMANOS else p.title()
                for p in base.replace("_", " ").split()]
    nombre = " ".join(palabras)
    if not re.search(r"[^\W\d_]", nombre):  # al menos una letra: "2012" no es una persona
        raise IdInvalido(f"No hay nombre de persona en '{fichero}'")
    return validar_id(nombre)


def main() -> int:
    parser = argparse.ArgumentParser(description="CSV id,foto desde una carpeta de fotos")
    parser.add_argument("carpeta", type=Path, help="carpeta con las fotos (persona_año.jpg)")
    parser.add_argument("--salida", type=Path, default=RAIZ / "data" / "dataset_ids.csv")
    args = parser.parse_args()

    if not args.carpeta.is_dir():
        print(f"No existe la carpeta {args.carpeta}", file=sys.stderr)
        return 1
    fotos = sorted(f for f in args.carpeta.iterdir()
                   if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in EXTENSIONES)
    if not fotos:
        print(f"No hay fotos ({', '.join(sorted(EXTENSIONES))}) en {args.carpeta}", file=sys.stderr)
        return 1

    filas = []
    for foto in fotos:
        try:
            nombre = nombre_de_fichero(foto.name)
        except IdInvalido as e:
            print(f"[saltada] {foto.name}: {e.mensaje}", file=sys.stderr)
            continue
        ruta = foto.resolve()
        try:
            ruta = ruta.relative_to(RAIZ)  # relativa a la raíz del repo si está dentro
        except ValueError:
            pass
        filas.append({"id": nombre, "foto": str(ruta)})

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    with args.salida.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=["id", "foto"])
        escritor.writeheader()
        escritor.writerows(filas)

    repetidos = sorted({fila["id"] for fila in filas if sum(g["id"] == fila["id"] for g in filas) > 1})
    print(f"{len(filas)} fotos de {len({f['id'] for f in filas})} personas -> {args.salida}")
    if repetidos:
        print(f"Varias fotos de la misma persona (serán muestras distintas): {', '.join(repetidos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
