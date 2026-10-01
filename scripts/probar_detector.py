"""Procesa una carpeta de imágenes y guarda las caras alineadas para revisarlas a ojo.

Uso:
    python scripts/probar_detector.py data/fotos
    python scripts/probar_detector.py data/fotos --salida caras_recortadas --anotadas
    python scripts/probar_detector.py data/fotos --recursivo

Genera en la carpeta de salida:
    caras/<nombre>.png      cara alineada (lo que recibiría el extractor)
    anotadas/<nombre>.jpg   (con --anotadas) imagen original con cajas, landmarks y confianza
    resumen.csv             una fila por imagen: resultado, confianza, nitidez, tamaño de cara

El resumen sirve también para calibrar `calidad.tam_min_cara` y
`calidad.umbral_borrosidad` de config.yaml viendo los valores reales de las fotos.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# biometria se importa ANTES que cv2: fija el límite de píxeles del decodificador
from biometria.camara import dibujar_detecciones  # noqa: E402
from biometria.config import cargar_config  # noqa: E402
from biometria.detector import Detector  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.imagen import cargar_imagen  # noqa: E402

import cv2  # noqa: E402,I001

EXTENSIONES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def listar_imagenes(carpeta: Path, recursivo: bool) -> list[Path]:
    patron = carpeta.rglob("*") if recursivo else carpeta.glob("*")
    return sorted(p for p in patron if p.is_file() and p.suffix.lower() in EXTENSIONES)


def nombre_salida(ruta: Path, carpeta_entrada: Path) -> str:
    """Nombre único y plano: subcarpeta/foto.jpg -> subcarpeta__foto"""
    relativa = ruta.relative_to(carpeta_entrada).with_suffix("")
    return "__".join(relativa.parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Revisa el detector sobre una carpeta de imágenes")
    parser.add_argument("entrada", type=Path, help="carpeta con imágenes")
    parser.add_argument("--salida", type=Path, default=Path("caras_recortadas"))
    parser.add_argument("--anotadas", action="store_true", help="guarda también las imágenes anotadas")
    parser.add_argument("--recursivo", action="store_true", help="busca también en subcarpetas")
    parser.add_argument("--config", default=None, help="fichero de configuración alternativo")
    args = parser.parse_args()

    if not args.entrada.is_dir():
        print(f"No existe la carpeta {args.entrada}", file=sys.stderr)
        return 1

    cfg = cargar_config(args.config)
    detector = Detector(cfg)
    imagenes = listar_imagenes(args.entrada, args.recursivo)
    if not imagenes:
        print(f"No hay imágenes en {args.entrada}", file=sys.stderr)
        return 1

    (args.salida / "caras").mkdir(parents=True, exist_ok=True)
    if args.anotadas:
        (args.salida / "anotadas").mkdir(parents=True, exist_ok=True)

    filas = []
    for ruta in imagenes:
        nombre = nombre_salida(ruta, args.entrada)
        fila = {"imagen": str(ruta), "resultado": "ok", "n_caras": "",
                "confianza": "", "lado_cara": "", "nitidez": "", "mensaje": ""}
        try:
            imagen = cargar_imagen(ruta, cfg.limites)
            detecciones = detector.detectar(imagen)
            fila["n_caras"] = len(detecciones)
            if detecciones:
                fila["confianza"] = f"{detecciones[0].confianza:.3f}"
                fila["lado_cara"] = min(detecciones[0].caja[2:])
            if args.anotadas:
                cv2.imwrite(str(args.salida / "anotadas" / f"{nombre}.jpg"),
                            dibujar_detecciones(imagen, detecciones))

            detector.elegir_cara(detecciones)
            procesada = detector.procesar_deteccion(imagen, detecciones[0])
            fila["nitidez"] = f"{procesada.nitidez:.1f}"
            cv2.imwrite(str(args.salida / "caras" / f"{nombre}.png"), procesada.cara)
        except ErrorBiometrico as e:
            fila["resultado"] = e.codigo
            fila["mensaje"] = e.mensaje
        filas.append(fila)
        print(f"{fila['resultado']:>24}  {ruta}")

    with (args.salida / "resumen.csv").open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)

    conteo = Counter(f["resultado"] for f in filas)
    print("\nResumen:", ", ".join(f"{k}={v}" for k, v in conteo.most_common()))
    print(f"Caras en {args.salida / 'caras'}  ·  detalle en {args.salida / 'resumen.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
