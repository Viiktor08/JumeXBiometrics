# Imagen lista para usar del sistema JumeXBiometrics.
#
#   docker compose up --build                                  # la web (JumeX Lens), solo en este equipo
#   docker compose exec web python -m biometria <comando> ...  # la CLI, con la misma BD
#   docker compose --profile pruebas run --rm pruebas          # los tests (imagen aparte)
#
# Tres etapas:
#   comun   — dependencias de ejecución, modelos verificados, cloudflared y el código
#   pruebas — comun + pytest y matplotlib (tests y scripts de evaluación)
#   final   — la imagen de uso (por defecto): sin dependencias de desarrollo
#
# La base de datos (data/lens/bd.csv) vive en el volumen /app/data y la clave maestra
# en OTRO volumen (~/.jumex), igual que fuera de Docker: quien lea o escriba los
# datos no tiene la clave. Ninguna de las dos va dentro de la imagen.

FROM python:3.11-slim AS comun

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

# 1) Dependencias de ejecución (capa cacheada mientras no cambie requirements.txt)
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

# 2) Modelos: se descargan y verifican por SHA256 durante el build
COPY scripts/descargar_modelos.py scripts/
RUN python scripts/descargar_modelos.py

# 3) cloudflared (binario estático de la imagen oficial) para publicar la web con
#    un túnel rápido de Cloudflare: `python -m biometria.web --tunel`
COPY --from=cloudflare/cloudflared:2026.10.0 /usr/local/bin/cloudflared /usr/local/bin/cloudflared

# 4) Código del sistema
COPY . .

# Usuario sin privilegios: si alguien explota el sistema no obtiene root en el contenedor.
# chmod a+rX: el código tiene que poder LEERLO ese usuario aunque en el equipo que
# construye la imagen algún fichero tenga permisos 600 (COPY conserva los permisos).
RUN chmod -R a+rX /app \
    && useradd --create-home --uid 1000 biometria \
    && mkdir -p /app/data /home/biometria/.jumex \
    && chmod 700 /home/biometria/.jumex \
    && chown -R biometria:biometria /app/data /app/tests /home/biometria/.jumex
VOLUME ["/app/data", "/home/biometria/.jumex"]


# --- Tests y evaluación: añade pytest y matplotlib (requirements-dev.txt) ---------
FROM comun AS pruebas
RUN pip install -r requirements-dev.txt
USER biometria
ENTRYPOINT ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


# --- Imagen de uso (la que se construye por defecto) --------------------------------
FROM comun AS final
USER biometria
ENTRYPOINT ["python", "-m", "biometria"]
