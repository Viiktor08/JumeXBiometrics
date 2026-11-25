"""Detección de caras en directo con la webcam (p. ej. la cámara del Mac).

Uso:
    python -m biometria.camara                 # cámara 0, umbral de config.yaml
    python -m biometria.camara --umbral 0.5    # muestra también caras con menos confianza
    python -m biometria.camara --camara 1      # otra cámara (p. ej. la del iPhone)

Teclas:  q / ESC salir   ·   s guardar foto completa + cara alineada en data/capturas/

Muestra cada cara con su confianza en [0, 1] (rojo = baja, verde = alta), sus
5 landmarks y, para la cara principal, si pasaría el control de calidad del
sistema (pequeña, borrosa, varias caras...). La ventanita "cara alineada" es
exactamente lo que recibiría el extractor de características.

Nota macOS: la primera vez el sistema pide permiso de cámara para la app
desde la que se lanza (Terminal, iTerm o VS Code). Si se deniega, hay que
activarlo en Ajustes del Sistema > Privacidad y seguridad > Cámara.
No funciona dentro de Docker en macOS (el contenedor no ve la cámara).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from biometria.config import Config, cargar_config
from biometria.detector import CaraProcesada, Deteccion, Detector
from biometria.errores import ErrorBiometrico

VENTANA_PRINCIPAL = "JumeXBiometrics - camara (q para salir, s para guardar)"
VENTANA_CARA = "cara alineada"


def color_confianza(confianza: float) -> tuple[int, int, int]:
    """Color BGR de rojo (confianza 0) a verde (confianza 1)."""
    c = float(np.clip(confianza, 0.0, 1.0))
    return (0, int(255 * c), int(255 * (1 - c)))


def _texto(imagen: np.ndarray, texto: str, posicion: tuple[int, int], escala: float,
           color: tuple[int, int, int], grosor: int) -> None:
    """Texto con contorno negro para que se lea sobre cualquier fondo."""
    cv2.putText(imagen, texto, posicion, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0),
                grosor + 2, cv2.LINE_AA)
    cv2.putText(imagen, texto, posicion, cv2.FONT_HERSHEY_SIMPLEX, escala, color,
                grosor, cv2.LINE_AA)


def dibujar_detecciones(imagen: np.ndarray, detecciones: list[Deteccion],
                        estado: str | None = None) -> np.ndarray:
    """Devuelve una copia de la imagen con cajas, landmarks y confianza [0, 1].

    `estado` es un texto opcional (resultado del control de calidad) que se
    escribe bajo la cara principal (la primera, la de mayor área).
    """
    salida = imagen.copy()
    escala_texto = max(0.5, min(salida.shape[:2]) / 900)
    grosor = max(1, round(escala_texto * 2))

    for i, det in enumerate(detecciones):
        x, y, w, h = det.caja
        color = color_confianza(det.confianza)
        cv2.rectangle(salida, (x, y), (x + w, y + h), color, grosor)
        for px, py in det.landmarks:
            cv2.circle(salida, (int(px), int(py)), grosor + 1, (255, 255, 0), -1)
        _texto(salida, f"{det.confianza:.2f}", (x, max(0, y - 8)), escala_texto, color, grosor)
        if i == 0 and estado:
            _texto(salida, estado, (x, y + h + int(25 * escala_texto)), escala_texto * 0.8,
                   color, grosor)
    return salida


def evaluar_frame(detector: Detector, frame: np.ndarray, umbral: float | None
                  ) -> tuple[list[Deteccion], CaraProcesada | None, str]:
    """Detecta caras en un frame y pasa el control de calidad a la principal.

    Returns:
        (detecciones, cara procesada o None, texto de estado)
    """
    detecciones = detector.detectar(frame, umbral)
    if not detecciones:
        return detecciones, None, ""
    try:
        detector.elegir_cara(detecciones)  # aplica la política de varias caras
        procesada = detector.procesar_deteccion(frame, detecciones[0])
        return detecciones, procesada, f"OK (nitidez {procesada.nitidez:.0f})"
    except ErrorBiometrico as e:
        return detecciones, None, e.codigo


def detectar_camara(indice: int = 0, umbral: float | None = None,
                    cfg: Config | None = None, carpeta_capturas: Path | None = None) -> None:
    """Abre la cámara y muestra en directo las caras detectadas con su confianza.

    Args:
        indice: índice de la cámara para OpenCV (0 = cámara integrada del Mac).
        umbral: confianza mínima en [0, 1] para mostrar una cara (None = config).
        cfg: configuración (None = config.yaml).
        carpeta_capturas: dónde guardar con la tecla 's' (None = data/capturas).
    """
    cfg = cfg or cargar_config()
    detector = Detector(cfg)
    carpeta_capturas = carpeta_capturas or cfg.bd.ruta.parent / "capturas"

    captura = cv2.VideoCapture(indice)
    if not captura.isOpened():
        raise RuntimeError(
            f"No se pudo abrir la cámara {indice}. En macOS revisa Ajustes del Sistema > "
            "Privacidad y seguridad > Cámara y da permiso a tu terminal o a VS Code."
        )

    instante_anterior = time.perf_counter()
    fps = 0.0
    try:
        while True:
            ok, frame = captura.read()
            if not ok or frame is None:
                print("No se recibe imagen de la cámara", file=sys.stderr)
                break

            detecciones, procesada, estado = evaluar_frame(detector, frame, umbral)

            # FPS suavizado para que el número no baile
            ahora = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(ahora - instante_anterior, 1e-6)
            instante_anterior = ahora

            vista = dibujar_detecciones(frame, detecciones, estado)
            _texto(vista, f"{len(detecciones)} cara(s)  {fps:.0f} FPS", (10, 30), 0.8,
                   (255, 255, 255), 2)
            cv2.imshow(VENTANA_PRINCIPAL, vista)
            if procesada is not None:
                cv2.imshow(VENTANA_CARA, cv2.resize(procesada.cara, None, fx=2, fy=2,
                                                    interpolation=cv2.INTER_NEAREST))

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), 27):
                break
            if tecla == ord("s"):
                _guardar_captura(carpeta_capturas, frame, procesada)
    finally:
        captura.release()
        cv2.destroyAllWindows()
        cv2.waitKey(1)  # en macOS hace falta para que las ventanas se cierren de verdad


def _guardar_captura(carpeta: Path, frame: np.ndarray, procesada: CaraProcesada | None) -> None:
    """Guarda la foto completa (para inscribir con el pipeline normal) y,
    si pasó el control de calidad, la cara alineada."""
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ruta_foto = carpeta / f"foto_{marca}.jpg"
    cv2.imwrite(str(ruta_foto), frame)
    print(f"Guardada {ruta_foto}")
    if procesada is not None:
        ruta_cara = carpeta / f"cara_{marca}.png"
        cv2.imwrite(str(ruta_cara), procesada.cara)
        print(f"Guardada {ruta_cara}")


def _umbral(valor: str) -> float:
    numero = float(valor)
    if not 0.0 <= numero <= 1.0:
        raise argparse.ArgumentTypeError("el umbral debe estar entre 0 y 1")
    return numero


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m biometria.camara",
                                     description="Detección de caras en directo con la webcam")
    parser.add_argument("--camara", type=int, default=0, help="índice de la cámara (0 = integrada)")
    parser.add_argument("--umbral", type=_umbral, default=None,
                        help="confianza mínima en [0, 1] (por defecto la de config.yaml)")
    parser.add_argument("--config", default=None, help="fichero de configuración alternativo")
    args = parser.parse_args(argv)

    try:
        detectar_camara(args.camara, args.umbral, cargar_config(args.config))
    except (ErrorBiometrico, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
