"""JumeXBiometrics: sistema de reconocimiento facial (práctica de Sistemas Biométricos, UMA).

Módulos (se irán añadiendo por fases):
    config       carga y validación de config.yaml
    errores      excepciones con código estable
    imagen       carga segura de imágenes
    detector     YuNet + alineado + control de calidad
    camara       detección en directo con la webcam
    extractores  extractores intercambiables (LBP, ...)    (fase extractor)
    bd           base de datos CSV con HMAC                (fase bd-csv)
    sistema      SistemaBiometrico con los 9 métodos       (fase sistema-cli)
    cli          interfaz de línea de comandos             (fase sistema-cli)
"""

import os

__version__ = "0.1.0"

# Límite duro de píxeles en el decodificador de OpenCV (protección frente a
# "bombas de descompresión": un PNG de pocos KB que ocupa GB al decodificarse).
# OpenCV lee esta variable UNA sola vez, al importar cv2, así que debe fijarse
# aquí, antes de que ningún módulo del paquete importe cv2. El límite
# configurable `limites.max_pixeles` se comprueba además tras decodificar.
LIMITE_DURO_PIXELES = 50_000_000
os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"] = str(LIMITE_DURO_PIXELES)
