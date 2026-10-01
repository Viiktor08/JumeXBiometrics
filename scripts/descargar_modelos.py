"""Descarga los modelos necesarios en models/ y verifica su SHA256.

Uso:
    python scripts/descargar_modelos.py            # descarga lo que falte
    python scripts/descargar_modelos.py --forzar   # vuelve a descargar todo

Solo usa la librería estándar para poder ejecutarse en el build de Docker
antes de copiar el resto del código. Verificar el hash evita que un modelo
corrupto o manipulado (p. ej. un ONNX sustituido en el camino) entre en el sistema.
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

CARPETA_MODELOS = Path(__file__).resolve().parent.parent / "models"

# nombre de fichero -> (URL, SHA256 esperado)
MODELOS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def descargar(nombre: str, url: str, hash_esperado: str, forzar: bool) -> bool:
    destino = CARPETA_MODELOS / nombre
    if destino.is_file() and not forzar and sha256(destino) == hash_esperado:
        print(f"[ok] {nombre} ya existe y su hash es correcto")
        return True

    print(f"[..] descargando {nombre}", flush=True)
    temporal = destino.with_suffix(destino.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, temporal)
    except OSError as e:
        print(f"[ERROR] no se pudo descargar {nombre}: {e}", file=sys.stderr)
        temporal.unlink(missing_ok=True)
        return False

    obtenido = sha256(temporal)
    if obtenido != hash_esperado:
        print(
            f"[ERROR] hash incorrecto para {nombre}\n"
            f"        esperado {hash_esperado}\n        obtenido {obtenido}",
            file=sys.stderr,
        )
        temporal.unlink(missing_ok=True)
        return False

    temporal.replace(destino)
    print(f"[ok] {nombre} descargado y verificado")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--forzar", action="store_true", help="vuelve a descargar aunque ya existan")
    args = parser.parse_args()

    CARPETA_MODELOS.mkdir(parents=True, exist_ok=True)
    ok = all(descargar(n, url, h, args.forzar) for n, (url, h) in MODELOS.items())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
