# JumeXBiometrics

Sistema de reconocimiento facial para la práctica de **Sistemas Biométricos**
(Grado en Ciberseguridad e IA, Universidad de Málaga). El enunciado está en
[`Prácticas.pdf`](Pra%CC%81cticas.pdf).

El sistema inscribe, verifica (1:1) e identifica (1:N) personas a partir de
fotos de cara. La base de datos es un `.csv`. Expone los 9 métodos que pide el
enunciado, tanto desde Python como desde la línea de comandos (también dentro
de Docker), para que el grupo rival pueda ejecutarlo y atacarlo.

> **Estado:** en construcción. La técnica de reconocimiento aún no está
> asignada. Mientras tanto se usa un extractor LBP **provisional** que se
> podrá sustituir cambiando una línea de `config.yaml`.

## Estado por fases

| Fase | Rama | Contenido | Estado |
|---|---|---|---|
| 1 | `setup-base` | Estructura, dependencias, Docker, configuración | ✅ |
| 2 | `detector` | YuNet, alineado por ojos y control de calidad | ⏳ |
| 3 | `extractor` | Interfaz de extractor y LBP provisional | ⏳ |
| 4 | `bd-csv` | Base de datos CSV con validación e integridad (HMAC) | ⏳ |
| 5 | `sistema-cli` | `SistemaBiometrico` con los 9 métodos y la CLI | ⏳ |
| 6 | `inscripcion-evaluacion` | Inscripción masiva; FAR/FRR/EER, DET/ROC, CMC | ⏳ |
| 7 | `seguridad` | `THREAT_MODEL.md` y contramedidas | ⏳ |

## Uso con Docker (recomendado)

```bash
docker build -t jumex-biometrics .

# data/ se monta como volumen: ahí viven bd.csv, la clave HMAC y las fotos
docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics
```

Ahora mismo el contenedor solo muestra el estado del entorno (versiones y si el
modelo del detector está presente). Los comandos de los 9 métodos se añadirán en
la fase 5 y tendrán esta forma:

```bash
docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics verificar --id "Persona" --foto data/foto.jpg
```

Para pasar los tests dentro del contenedor:

```bash
docker run --rm --entrypoint python jumex-biometrics -m pytest -q
```

## Uso en local

Requisitos: **Python 3.11** (es la versión de Docker; otras versiones pueden
dar resultados ligeramente distintos).

```bash
python3.11 -m venv .venv
source .venv/bin/activate            # en Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python scripts/descargar_modelos.py  # baja YuNet a models/ y verifica su SHA256
python -m biometria                  # comprueba el entorno
pytest -q
```

## Estructura del repositorio

```
├── biometria/              paquete principal
│   ├── config.py           carga y validación de config.yaml
│   ├── errores.py          excepciones con código estable (se devuelven en la CLI)
│   ├── extractores/        extractores intercambiables (LBP provisional, ...)
│   └── __main__.py         punto de entrada: python -m biometria
├── scripts/
│   └── descargar_modelos.py
├── tests/                  tests con pytest
├── data/                   (fuera de git) fotos, bd.csv y clave HMAC
├── models/                 (fuera de git) modelos descargados
├── config.yaml             parámetros del sistema
├── Dockerfile
└── requirements*.txt       dependencias con versiones fijadas
```

## Configuración

Todo lo ajustable está en [`config.yaml`](config.yaml): detector, tamaño de la
cara, control de calidad, extractor, base de datos, umbrales y límites. Para
usar otro fichero: `BIOMETRIA_CONFIG=/ruta/otra.yaml`.

La clave HMAC de la base de datos **no está en el repositorio**. Se lee de la
variable de entorno `BIOMETRIA_HMAC_KEY` o, si no existe, de `data/clave_hmac.key`.

## Decisiones de diseño

Decisiones tomadas donde el enunciado es ambiguo o deja libertad:

1. **`verificar_t` e `identificar_t` reciben un template, no una foto.** El
   enunciado dice "a partir de una foto", pero también dice que omiten el
   extractor, así que lo interpretamos como una errata.
2. **Los métodos `_sd` asumen que la foto ya es una cara recortada.** Sin detector
   no hay landmarks, así que no se alinea: la imagen se pasa a escala de grises
   y se redimensiona al tamaño de cara configurado. Esto es una superficie de
   ataque conocida y se documentará en `THREAT_MODEL.md`.
3. **Detector YuNet (OpenCV) en vez de MTCNN.** Viene integrado en OpenCV
   (`cv2.FaceDetectorYN`), pesa unos 230 KB, no necesita PyTorch ni TensorFlow y da 5
   landmarks. Se alinea con la plantilla estándar de ArcFace de 112x112, así el
   recorte sirve tanto para LBP/LDA como para ArcFace.
4. **Si hay varias caras en la foto, por defecto se rechaza** (`varias_caras: error`).
   Así se evita inscribir o verificar a la persona equivocada. Se puede cambiar
   a `mayor` para quedarse con la cara más grande.
5. **Formato del CSV:** `id,template,hmac`, con una fila por muestra (varias
   muestras por persona). El template va en base64 de float32 little-endian.
   La puntuación frente a una persona es la máxima entre sus muestras
   (configurable a media).
6. **Integridad del CSV con HMAC-SHA256** (siempre activa) y **cifrado de
   templates con AES-GCM** (implementado pero desactivado por defecto con
   `cifrar_templates: false`).
7. **Umbrales provisionales (0.5).** Se calibrarán con el script de evaluación
   cuando tengamos las fotos del profesor.
8. **Versiones fijadas para Python 3.11:** numpy 2.4.x (la 2.5 ya exige 3.12) y
   OpenCV 4.14 headless (sin dependencias gráficas).

## Problemas frecuentes

- **`CERTIFICATE_VERIFY_FAILED` al descargar modelos en macOS:** el Python de
  python.org no trae certificados. Ejecuta
  `/Applications/Python\ 3.11/Install\ Certificates.command` o lanza el script
  con `SSL_CERT_FILE=/etc/ssl/cert.pem`.
- **El repo está en OneDrive/Dropbox:** crea el entorno virtual fuera de la
  carpeta sincronizada para evitar conflictos de sincronización.
