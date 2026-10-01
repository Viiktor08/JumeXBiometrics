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
| 3 | `extractor` | Interfaz de extractor y LBP provisional | ✅ |
| 4 | `bd-csv` | Base de datos CSV con validación e integridad (HMAC) | ✅ |
| 5 | `sistema-cli` | `SistemaBiometrico` con los 9 métodos y la CLI | ✅ |
| 6 | `inscripcion-evaluacion` | Inscripción masiva; FAR/FRR/EER, DET/ROC, CMC | ⏳ |
| 7 | `seguridad` | `THREAT_MODEL.md` y contramedidas | ⏳ |

## Uso con Docker (recomendado)

```bash
docker build -t jumex-biometrics .

# data/ se monta como volumen: ahí viven bd.csv, la clave HMAC y las fotos
docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics
```

Sin argumentos muestra el estado del entorno. Los comandos son los mismos que
en local (ver [Uso de la CLI](#uso-de-la-cli)); las fotos tienen que estar dentro
de `data/` para que el contenedor las vea:

```bash
docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics inscribir --id "Ana López" --foto data/fotos/ana.jpg
docker run --rm -v "$(pwd)/data:/app/data" jumex-biometrics verificar --id "Ana López" --foto data/fotos/ana_2.jpg
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
python -m biometria                  # comprueba el entorno (ver "Uso de la CLI")
pytest -q
```

## Uso de la CLI

Todos los comandos imprimen **una línea JSON**. Código de salida: `0` si la
operación se ha ejecutado (una verificación rechazada también da 0: mira
`aceptado`), `1` si hay un error controlado y `2` si faltan argumentos.

| Comando | Qué hace |
|---|---|
| `inscribir --id ID --foto F` | Inscribe una muestra (detector → calidad → alineado → extractor → BD) |
| `verificar --id ID --foto F` | 1:1, ¿es ID la persona de la foto? |
| `identificar --foto F` | 1:N, ¿quién es? (`null` si nadie supera el umbral) |
| `inscribir_sd` / `verificar_sd` / `identificar_sd` | Igual, pero la foto **ya es una cara recortada**: sin detector ni alineado |
| `inscribir_t --id ID --template T` / `verificar_t` / `identificar_t --template T` | Reciben un **template**: sin detector ni extractor |
| `extraer --foto F --salida T [--sin-detector]` | Guarda el template de una foto (para usar con los `_t`) |
| `bd` | Verifica la integridad de la BD y lista los inscritos |
| `estado` (o sin comando) | Versiones y estado del entorno |

```bash
python -m biometria inscribir --id "Ana López" --foto data/fotos/ana.jpg
# {"operacion": "inscribir", "id": "Ana López", "muestras": 1}

python -m biometria verificar --id "Ana López" --foto data/fotos/ana_2.jpg
# {"operacion": "verificar", "id": "Ana López", "aceptado": true, "puntuacion": 0.81, "umbral": 0.5}

python -m biometria identificar --foto data/fotos/quien.jpg
# {"operacion": "identificar", "identificado": true, "id": "Ana López", "puntuacion": 0.81, "umbral": 0.5}

python -m biometria extraer --foto data/fotos/ana.jpg --salida ana.npy
python -m biometria verificar_t --id "Ana López" --template ana.npy

python -m biometria verificar --id "Nadie" --foto data/fotos/ana.jpg
# {"error": "id_no_inscrito", "mensaje": "'Nadie' no está inscrito"}
```

Los templates (`--template`, `--salida`) pueden ser `.npy` (float32; nunca se
cargan objetos con pickle), `.json` (lista de números) o texto con el formato de
la BD (`f32:` + base64). Códigos de error posibles: `imagen_invalida`,
`imagen_demasiado_grande`, `sin_cara`, `varias_caras`, `cara_pequena`,
`imagen_borrosa`, `template_invalido`, `id_invalido`, `id_no_inscrito`,
`limite_muestras`, `bd_corrupta`, `integridad_bd` y `configuracion_invalida`.

Desde Python:

```python
from biometria.sistema import SistemaBiometrico

sistema = SistemaBiometrico()                       # o SistemaBiometrico(cargar_config("otra.yaml"))
sistema.inscribir("Ana López", "data/fotos/ana.jpg")  # la foto: ruta, bytes o array BGR
sistema.verificar("Ana López", "data/fotos/ana_2.jpg")
# ResultadoVerificacion(id='Ana López', aceptado=True, puntuacion=0.81, umbral=0.5)
sistema.identificar("data/fotos/quien.jpg")
# ResultadoIdentificacion(id='Ana López', puntuacion=0.81, umbral=0.5)
t = sistema.extraer("data/fotos/ana.jpg")
sistema.verificar_t("Ana López", t)
```

> **Aviso:** con el LBP provisional y el umbral de 0,5, `verificar` acepta
> prácticamente a cualquiera (ver [Extractor](#extractor)). El umbral se
> calibrará en la fase 6.

## Demo web: JumeX Lens

Interfaz web para ver el sistema funcionando: soltar una foto para **verificar**,
**identificar** o **inscribir**, con la animación de detección, cada paso del
pipeline (detección → alineado → template → comparación) y una galería de la
**base de datos**.

```bash
python scripts/descargar_modelos.py                                     # YuNet + SFace
python scripts/inscribir_csv.py data/lfw/personas.csv --config config_lens.yaml   # 50 personas de LFW
python -m biometria.web                                                 # abre http://127.0.0.1:8000
```

**Publicarla en internet (Cloudflare Tunnel):**

```bash
brew install cloudflared            # una vez
python -m biometria.web --tunel     # escribe la URL https://<algo>.trycloudflare.com
```

El servidor sigue en este equipo y Cloudflare reenvía las visitas: la web solo
está online mientras el comando siga abierto, y la URL cambia cada vez que se
arranca. **No tiene contraseña**: cualquiera con la URL puede ver las caras,
inscribir y borrar. Se acepta solo el dominio `trycloudflare.com` además de
localhost, y se sigue exigiendo la cabecera `X-JumeX-Lens`.

- Usa [`config_lens.yaml`](config_lens.yaml): extractor **SFace** (red de OpenCV
  Zoo, referencia fuerte), BD propia en `data/lens/` y umbrales 0,68
  (verificación) y 0,70 (identificación). En 144 personas de LFW: EER 1,4 % y
  rank-1 98,6 %, frente al 37 % y 11 % del LBP. Con `--config config.yaml` se usa el LBP.
- Fotos de prueba en `data/lfw/pruebas/`: `inscritos/` (otras fotos de personas
  de la BD) y `desconocidos/` (famosos que no están).
- Guarda además un retrato por persona en `data/lens/caras/` **solo para la
  interfaz**; el sistema compara siempre templates.
- **Si el detector no encuentra la cara o elige otra**, se puede marcar a mano
  arrastrando un recuadro (o pulsando una de las caras detectadas). Se vuelve a
  buscar la cara dentro del recuadro para alinearla con sus 5 puntos; si ni así
  aparece, se usa el recuadro sin alinear y se avisa de que será menos fiable.
- **Inscribir pide confirmación** ("¿Es esta la cara de X?") antes de guardar.
- **Identificar con varias personas en la foto:** cada cara (hasta 12) se alinea,
  se convierte en su template y se identifica por separado; el resultado dice
  quién es cada una o que no está guardada, y las desconocidas se pueden
  inscribir desde ahí.
- **Ficha de cada persona** (pestaña Base de datos): ver sus fotos (una por
  muestra), añadir otra, borrar una, elegir la foto principal, cambiar el nombre
  o eliminarla. Las fotos se guardan en `data/lens/caras/<persona>/<muestra>.jpg`,
  donde `<muestra>` es un hash del template de esa fila de `bd.csv`.
- Solo escucha en `127.0.0.1`, exige la cabecera `X-JumeX-Lens` en los POST y
  DELETE y rechaza un `Host` distinto de localhost (CSRF y DNS rebinding).

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

## Extractor

El extractor convierte la cara alineada en un **template** (vector float32) y
compara dos templates con una **similitud en [0, 1]** (1 = idénticos). Se elige
en `config.yaml` (`extractor.nombre` y `extractor.parametros`). El resto del
sistema solo usa la interfaz común de `biometria/extractores/base.py`, así que
cambiar de técnica no toca nada más.

El extractor actual es **LBP uniforme por rejilla** (Ahonen et al., 2006),
**provisional** hasta que nos asignen la técnica:

- radio 1, 8 vecinos y rejilla de 7x7 celdas → 59 clases × 49 celdas = **2891 valores**;
- cada celda es un histograma normalizado y el template completo suma 1;
- similitud = 1 − χ²/2, donde χ² es la distancia chi-cuadrado (entre 0 y 2).

Para comparar fotos a mano (pasan por detector → calidad → alineado → extractor):

```bash
python scripts/comparar_caras.py data/fotos/a.jpg data/fotos/b.jpg   # una puntuación
python scripts/comparar_caras.py data/lfw/fotos/*.jpg                # matriz todas contra todas
```

Desde Python:

```python
from biometria.extractores import crear_extractor

extractor = crear_extractor(cfg)
t1 = extractor.extraer(detector.procesar(imagen1).cara)
t2 = extractor.extraer(detector.procesar(imagen2).cara)
extractor.comparar(t1, t2)             # -> 0.73
extractor.validar_template(lista)      # TemplateInvalido si no es un template LBP válido
```

**Rendimiento orientativo del LBP en LFW** (144 personas con 2 fotos, `varias_caras: mayor`):

| Medida | Valor |
|---|---|
| Similitud media, misma persona | 0,70 |
| Similitud media, personas distintas | 0,68 |
| EER | ≈ 37 % (umbral ≈ 0,69) |
| Identificación rank-1 entre 144 | 11 % (azar: 0,7 %) |

Distingue algo, pero poco: LFW tiene mucha variación de pose, luz y expresión, y
cambiar radio, vecinos o rejilla apenas lo mejora (EER entre 37 y 39 %). **Con
el umbral provisional de 0,5 se aceptaría a cualquiera** (FAR del 100 %): hay que
calibrarlo en la fase 6 con las fotos del profesor.

## Base de datos

Los templates se guardan en `data/bd.csv` (fuera de git), una fila por muestra:

```
id,template,hmac
Afton Smith,f32:BS+nOAAAAAAAAAAA...,4c1f9e...
Afton Smith,f32:AAAgPwAAQD8AAAAA...,b72e04...
Allyson Felix,f32:AADAPgAAAD8AAAA...,51d9aa...
```

- **`template`**: `f32:` + base64 de los 2891 float32 (little-endian), o `gcm:` +
  base64 del template cifrado con AES-256-GCM si `bd.cifrar_templates: true`.
- **`hmac`**: HMAC-SHA256 de la fila **junto con su posición y el número total de
  filas**. Cambiar un template, cambiar un id, mover un template a otra persona,
  borrar, duplicar o reordenar filas da `integridad_bd`.
- Cada operación vuelve a leer y verificar el CSV entero, y la escritura es
  atómica (fichero temporal + renombrado).
- **Clave:** variable de entorno `BIOMETRIA_HMAC_KEY` (mínimo 32 caracteres) o
  el fichero `data/clave_hmac.key`, que se genera solo, con permisos `600`, la
  primera vez que se inscribe a alguien. **Si se pierde la clave, la BD no se
  puede verificar** y hay que volver a inscribir.

Para comprobar la integridad y ver quién está inscrito:

```bash
python -m biometria bd
# {"operacion": "bd", "integridad": "ok", "personas": 41, "muestras": 41, "ids": {...}}
```

Desde Python:

```python
from biometria.bd import BaseDatos

bd = BaseDatos(cfg, extractor)
bd.inscribir("Ana López", template)    # -> nº de muestras de Ana (máx. limites.max_muestras_por_id)
bd.templates_de("Ana López")           # [template, ...]  (IdNoInscrito si no está)
bd.por_id()                            # {id: [templates]}
```

Los ids deben empezar por letra o número y solo pueden llevar letras (con
acentos y ñ), números, espacios y `. - _ '` (máximo 100 caracteres).

## Estructura del repositorio

```
├── biometria/              paquete principal
│   ├── config.py           carga y validación de config.yaml
│   ├── errores.py          excepciones con código estable (se devuelven en la CLI)
│   ├── imagen.py           carga segura de imágenes (límites de bytes y píxeles)
│   ├── detector.py         YuNet + alineado + control de calidad
│   ├── camara.py           demo en directo con la webcam
│   ├── bd.py               base de datos CSV con HMAC y cifrado opcional
│   ├── sistema.py          SistemaBiometrico: los 9 métodos
│   ├── cli.py              línea de comandos (salida JSON)
│   ├── web/                demo web JumeX Lens (servidor + interfaz)
│   ├── extractores/        extractores intercambiables
│   │   ├── base.py         interfaz común: extraer, comparar, validar_template
│   │   ├── lbp.py          LBP uniforme por rejilla (provisional)
│   │   └── sface.py        SFace de OpenCV Zoo (referencia, demo web)
│   └── __main__.py         punto de entrada: python -m biometria
├── scripts/
│   ├── descargar_modelos.py
│   ├── probar_detector.py  procesa una carpeta y guarda las caras para revisarlas
│   ├── preparar_lfw.py     50 personas de LFW para probar mientras no hay fotos
│   └── comparar_caras.py   similitud entre las caras de varias fotos
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
   muestras por persona). El template va en base64 de float32 little-endian,
   con un prefijo (`f32:` o `gcm:`) que dice si está cifrado, para que activar
   el cifrado no rompa una BD ya creada. La puntuación frente a una persona es
   la máxima entre sus muestras (configurable a media).
6. **Integridad del CSV con HMAC-SHA256** (siempre activa) y **cifrado de
   templates con AES-GCM** (implementado pero desactivado por defecto con
   `cifrar_templates: false`). Las claves del HMAC y del cifrado se derivan
   con HKDF de una única clave maestra. En el cifrado, el id va como dato
   asociado: un template cifrado no se puede mover a otra persona.
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
    Además se detecta también a 640 y 320 px y se combinan los resultados: YuNet
    no ve las caras que ocupan más del ~40 % del ancho de su entrada (un retrato
    de primer plano), y en las copias pequeñas esas caras sí tienen un tamaño que
    reconoce.

14. **Toda puntuación de similitud está en [0, 1]** y "más alto = más parecido",
    sea cual sea el extractor. Así los umbrales de `config.yaml` significan lo
    mismo con LBP que con la técnica definitiva.
15. **Los templates que llegan de fuera se validan** (`validar_template`):
    dimensión exacta, sin NaN ni infinitos y, en LBP, valores no negativos y
    cada celda normalizada. Esto filtra basura en los métodos `_t` y en la BD,
    pero **no impide** que un atacante fabrique un template válido a medida;
    eso irá en `THREAT_MODEL.md`.
16. **LBP implementado con numpy, sin scikit-image**, para no añadir una
    dependencia pesada. Se ha comparado con `skimage.feature.local_binary_pattern`:
    los códigos coinciden salvo en empates exactos vecino = centro tras la
    interpolación, que aquí siempre cuentan como 1 (definición original de LBP)
    y en scikit-image dependen del redondeo.
17. **`rejilla` es [columnas, filas]**, en el mismo orden que `tam_cara` ([ancho, alto]).

18. **El HMAC de cada fila incluye su posición y el total de filas.** Con un HMAC
    por fila sin más, un atacante con escritura podría borrar, duplicar o
    reordenar filas sin que se notara. Así se detecta todo eso sin un segundo
    fichero de firmas. El coste es volver a firmar todas las filas al inscribir,
    algo despreciable con 51 personas.
19. **Lo que la integridad NO detecta** (irá en `THREAT_MODEL.md`): volver a una
    copia antigua completa del CSV (rollback) y borrar el fichero entero, que
    equivale a una BD vacía. Es denegación de servicio, pero no permite
    suplantar a nadie. Quien tenga la clave puede, por supuesto, firmar lo que
    quiera.
20. **Ids restringidos:** empiezan por letra o número (evita la inyección de
    fórmulas al abrir el CSV con Excel: `=`, `+`, `-`, `@`), sin saltos de línea
    ni caracteres de control, y normalizados a Unicode NFC para que "José"
    escrito de dos formas sea el mismo id.
21. **`bd.csv` y la clave se crean con permisos `600`**: solo los lee el usuario
    que ejecuta el sistema.

22. **Puntuación frente a una persona = máximo entre sus muestras**
    (`bd.agregacion: max`, o `media`). En identificación gana la persona con la
    puntuación más alta; si no llega al umbral, el resultado es `null`
    (desconocido), pero se informa igualmente de la mejor puntuación.
23. **Se devuelve la puntuación**, no solo la decisión, porque hace falta para la
    evaluación (curvas DET/ROC) y para que el rival pueda medir el sistema. A
    cambio facilita ataques de *hill-climbing* (ir modificando un template en
    `verificar_t` hasta que suba la puntuación); irá en `THREAT_MODEL.md`.
24. **`verificar` comprueba que el id está inscrito antes de procesar la foto**,
    así un id inexistente responde rápido y con un error claro.
25. **Comando extra `extraer`**: no lo pide el enunciado, pero sin él no hay
    forma cómoda de obtener templates para probar los métodos `_t`.
26. **Los métodos `_t` no cargan el detector**: funcionan aunque falte el modelo YuNet.

## Problemas frecuentes

- **`CERTIFICATE_VERIFY_FAILED` al descargar modelos en macOS:** el Python de
  python.org no trae certificados. Ejecuta
  `/Applications/Python\ 3.11/Install\ Certificates.command` o lanza el script
  con `SSL_CERT_FILE=/etc/ssl/cert.pem`.
- **El repo está en OneDrive/Dropbox:** crea el entorno virtual fuera de la
  carpeta sincronizada para evitar conflictos de sincronización.
