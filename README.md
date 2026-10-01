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
| 2 | `detector` | YuNet, alineado por ojos, control de calidad y demo de cámara | ✅ |
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

## Detector y cámara

Demo en directo con la webcam (solo en local; Docker en macOS no ve la cámara):

```bash
python -m biometria.camara                # umbral de confianza de config.yaml
python -m biometria.camara --umbral 0.5   # muestra también caras con menos confianza
```

Cada cara aparece con su **confianza en [0, 1]** (rojo = baja, verde = alta), sus
5 landmarks y, para la cara principal, el resultado del control de calidad
(`OK`, `cara_pequena`, `imagen_borrosa`, `varias_caras`). La ventana
"cara alineada" muestra exactamente lo que recibe el extractor. Con `s` se
guardan la foto y la cara en `data/capturas/` (útil para inscribir a nuestra
persona número 51); con `q` se sale. La primera vez macOS pedirá permiso de
cámara para Terminal o VS Code.

Para revisar a ojo el detector sobre una carpeta de fotos:

```bash
python scripts/probar_detector.py data/fotos --anotadas --recursivo
# -> caras_recortadas/caras/*.png, caras_recortadas/anotadas/*.jpg y resumen.csv
```

`resumen.csv` incluye la confianza, el tamaño de la cara y la nitidez de cada
foto, para calibrar `calidad.tam_min_cara` y `calidad.umbral_borrosidad`.

Mientras no tengamos las 50 fotos del profesor, se puede probar con 50 personas
de [LFW](https://vis-www.cs.umass.edu/lfw/) (Labeled Faces in the Wild):

```bash
python scripts/preparar_lfw.py      # data/lfw/fotos/*.jpg y data/lfw/personas.csv (id,foto)
python scripts/probar_detector.py data/lfw/fotos --anotadas --salida caras_lfw
```

Resultado con la semilla por defecto: cara detectada en las 50 fotos, sin falsos
positivos. Con `varias_caras: error` se rechazan 8 por tener gente al fondo; con
`mayor` pasan y siempre se elige a la persona correcta. 1 o 2 se quedan justo por
debajo del umbral de nitidez (43–45 frente a 50) por la compresión JPEG de LFW.

Desde Python:

```python
from biometria.config import cargar_config
from biometria.detector import Detector
from biometria.imagen import cargar_imagen

cfg = cargar_config()
detector = Detector(cfg)
imagen = cargar_imagen("foto.jpg", cfg.limites)
detector.detectar(imagen)              # [Deteccion(caja, landmarks, confianza), ...]
detector.procesar(imagen).cara         # cara alineada 112x112 (o error claro)
```

## Estructura del repositorio

```
├── biometria/              paquete principal
│   ├── config.py           carga y validación de config.yaml
│   ├── errores.py          excepciones con código estable (se devuelven en la CLI)
│   ├── imagen.py           carga segura de imágenes (límites de bytes y píxeles)
│   ├── detector.py         YuNet + alineado + control de calidad
│   ├── camara.py           demo en directo con la webcam
│   ├── extractores/        extractores intercambiables (LBP provisional, ...)
│   └── __main__.py         punto de entrada: python -m biometria
├── scripts/
│   ├── descargar_modelos.py
│   └── probar_detector.py  procesa una carpeta y guarda las caras para revisarlas
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
   OpenCV 4.14 **completo** (no headless), porque la demo de cámara necesita
   `cv2.imshow`.
9. **Alineado con los 5 landmarks**, no solo con los ojos: es una transformación
   de similitud (rotación + escala + traslación) estimada con LMEDS, más estable
   si un punto sale mal. Los ojos quedan horizontales y siempre a la misma altura.
10. **Los métodos `_sd` sí pasan el control de nitidez.** Omiten el detector, pero
    no el control de calidad. Así una imagen plana o vacía no llega al extractor.
11. **Control de calidad tras detectar:** el tamaño mínimo se mide sobre la caja
    en la imagen original y la nitidez (varianza del Laplaciano) sobre la cara
    ya alineada, para que no dependa de la resolución de la foto.
12. **Doble límite de píxeles:** OpenCV rechaza al leer la cabecera las imágenes
    de más de 50 Mpx, sin llegar a decodificarlas (`OPENCV_IO_MAX_IMAGE_PIXELS`,
    fijado en `biometria/__init__.py`). Además se aplica `limites.max_pixeles`
    de la configuración. Por eso hay que **importar `biometria` antes que `cv2`**.
13. **Imágenes grandes:** se detecta sobre una copia reducida a 1280 px de lado,
    pero la cara se recorta de la imagen original a resolución completa.

## Problemas frecuentes

- **`CERTIFICATE_VERIFY_FAILED` al descargar modelos en macOS:** el Python de
  python.org no trae certificados. Ejecuta
  `/Applications/Python\ 3.11/Install\ Certificates.command` o lanza el script
  con `SSL_CERT_FILE=/etc/ssl/cert.pem`.
- **El repo está en OneDrive/Dropbox:** crea el entorno virtual fuera de la
  carpeta sincronizada para evitar conflictos de sincronización.
