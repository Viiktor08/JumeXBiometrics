"""JumeXBiometrics: sistema de reconocimiento facial (práctica de Sistemas Biométricos, UMA).

Módulos (se irán añadiendo por fases):
    config       carga y validación de config.yaml
    errores      excepciones con código estable
    imagen       carga segura de imágenes                  (fase detector)
    detector     YuNet + alineado + control de calidad     (fase detector)
    extractores  extractores intercambiables (LBP, ...)    (fase extractor)
    bd           base de datos CSV con HMAC                (fase bd-csv)
    sistema      SistemaBiometrico con los 9 métodos       (fase sistema-cli)
    cli          interfaz de línea de comandos             (fase sistema-cli)
"""

__version__ = "0.1.0"
