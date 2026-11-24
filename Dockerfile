# Imagen lista para usar del sistema JumeXBiometrics.
#
#   docker build -t jumex-biometrics .
#   docker run --rm -v "$(pwd)/data:/app/data" -v "$HOME/.jumex:/home/biometria/.jumex" \
#       jumex-biometrics <comando> [args]
#
# La web (JumeX Lens) con URL pública se levanta con docker-compose.yml:
#   docker compose up --build
#
# La base de datos (data/lens/bd.csv) vive en el volumen /app/data y la clave maestra
# en OTRO volumen (~/.jumex), igual que fuera de Docker: quien lea o escriba los
# datos no tiene la clave. Ninguna de las dos va dentro de la imagen.

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

# 3) cloudflared (binario estático de la imagen oficial) para publicar la web con
#    un túnel rápido de Cloudflare: `python -m biometria.web --tunel`
COPY --from=cloudflare/cloudflared:2026.10.0 /usr/local/bin/cloudflared /usr/local/bin/cloudflared

# 4) Código del sistema
COPY . .

# Usuario sin privilegios: si alguien explota el sistema no obtiene root en el contenedor
RUN useradd --create-home --uid 1000 biometria \
    && mkdir -p /app/data /home/biometria/.jumex \
    && chmod 700 /home/biometria/.jumex \
    && chown -R biometria:biometria /app/data /app/tests /home/biometria/.jumex
USER biometria

VOLUME ["/app/data", "/home/biometria/.jumex"]

ENTRYPOINT ["python", "-m", "biometria"]
