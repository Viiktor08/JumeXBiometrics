"""Punto de entrada: `python -m biometria ...` (también el ENTRYPOINT de Docker).

De momento solo comprueba que el entorno está bien montado. La CLI completa
con los 9 métodos llegará en la fase `sistema-cli`.
"""

import json
import sys

import cv2
import numpy as np

from biometria import __version__
from biometria.config import cargar_config
from biometria.errores import ErrorBiometrico


def main() -> int:
    try:
        cfg = cargar_config()
    except ErrorBiometrico as e:
        print(json.dumps(e.a_dict(), ensure_ascii=False))
        return 1

    estado = {
        "version": __version__,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "modelo_detector": str(cfg.detector.modelo),
        "modelo_detector_presente": cfg.detector.modelo.is_file(),
        "extractor": cfg.extractor.nombre,
        "aviso": "CLI de los 9 métodos pendiente (fase sistema-cli)",
    }
    print(json.dumps(estado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
