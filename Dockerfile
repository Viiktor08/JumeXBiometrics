# Imagen lista para usar del sistema JumeXBiometrics.
#
#   docker build -t jumex-biometrics .
#   docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics <comando> [args]
#
# La base de datos (data/bd.csv) y la clave HMAC viven en el volumen /app/data,
# no dentro de la imagen.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Librerías del sistema que necesita opencv-python (versión completa, por la
# demo de cámara) para poder importarse en una imagen slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Dependencias de Python (capa cacheada mientras no cambie requirements.txt)
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# 2) Modelos: se descargan y verifican por SHA256 durante el build
COPY scripts/descargar_modelos.py scripts/
RUN python scripts/descargar_modelos.py

# 3) Código del sistema
COPY . .

# Usuario sin privilegios: si alguien explota el sistema no obtiene root en el contenedor
RUN useradd --create-home --uid 1000 biometria \
    && mkdir -p /app/data \
    && chown -R biometria:biometria /app/data
USER biometria

VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "biometria"]
