# JumeXBiometrics: guía del proyecto

Esta guía explica **qué es el proyecto, cómo se lanza, cómo funciona por dentro y
cómo lo hemos protegido** frente a los ataques del otro equipo. Está pensada para
leerla de principio a fin una vez y luego volver a la sección que haga falta.
Para el detalle técnico de cada defensa: [`THREAT_MODEL.md`](THREAT_MODEL.md). Para
la referencia completa (todas las opciones y comandos): [`README.md`](README.md).

---

## 1. Qué es, en una frase

Un **sistema de reconocimiento facial en Python** que inscribe personas, las
**verifica** ("¿esta foto es de Ana?") y las **identifica** ("¿quién es esta
persona?"), con una base de datos CSV protegida, una línea de comandos para que
el profesor y el equipo rival lo usen y lo ataquen, y una web (**JumeX Lens**)
para enseñarlo.

Cumple lo que pide el enunciado de *Sistemas Biométricos*: Python, base de datos
en CSV, los **9 métodos** (`inscribir`, `verificar`, `identificar`, y sus variantes
`_sd` y `_t`), las personas del Dataset inscritas y una evaluación de seguridad
que supone un atacante que **se salta partes, lee la base de datos o escribe en
ella**.

---

## 2. Lanzarlo en local

### 2.1 La primera vez

Necesitas **Python 3.11 o superior** y `git`. La carpeta **`Dataset/`** (las 49 fotos
que entregó el docente y la de Lionel Messi, nuestra persona elegida) **viene en el repositorio**, para que `docker compose up`
cree la base de datos solo. Lo más fácil es no instalar nada y usar Docker
(`docker compose up --build`, ver el README); esto es para trabajar sin Docker.

```bash
# 1) Clonar FUERA de OneDrive/iCloud/Dropbox (rompen la carpeta .git)
mkdir -p ~/dev && cd ~/dev
git clone -b main https://github.com/Viiktor08/JumeXBiometrics.git
cd JumeXBiometrics

# 2) Entorno virtual (también fuera de carpetas sincronizadas)
python3 -m venv ~/.venvs/jumex
source ~/.venvs/jumex/bin/activate
pip install -r requirements-dev.txt

# 3) Modelos (~290 MB la primera vez; se comprueba su SHA256)
python scripts/descargar_modelos.py

# 4) Comprobar que todo está bien
pytest -q                                   # deben pasar todos los tests

# 5) Cargar la base de datos con las fotos de Dataset/ (en data/lens/)
python scripts/preparar_dataset.py Dataset  # crea data/dataset_ids.csv (nombre de cada foto)
python scripts/inscribir_csv.py data/dataset_ids.csv

# 6) Lanzar la web
python -m biometria.web --admin-pass UN_PIN_LARGO   # abre http://127.0.0.1:8000
```

La **clave** de la base de datos y el **sello** anti-rollback se crean solos en
`~/.jumex/` la primera vez que se inscribe a alguien. No hay que hacer nada.

### 2.2 Para actualizar (si ya lo tienes clonado)

```bash
cd ~/dev/JumeXBiometrics
git switch main && git pull
source ~/.venvs/jumex/bin/activate
pip install -r requirements-dev.txt         # por si cambian dependencias
python scripts/descargar_modelos.py         # solo baja lo que falte
python -m biometria.web --admin-pass UN_PIN_LARGO
```

**Solo una vez, si tenías una base de datos de antes** (cuando la clave estaba en
`data/`): hay que pasarla al formato protegido. Si no, la BD dará error de integridad.

```bash
python scripts/proteger_bd.py --config config_lens.yaml --clave-antigua data/lens/clave_hmac.key
```

Mueve la clave a `~/.jumex/`, cifra la base de datos y crea el sello.

### 2.3 Publicarla en internet (opcional)

```bash
brew install cloudflared                                    # una vez (macOS)
python -m biometria.web --tunel --admin-pass UN_PIN_LARGO   # escribe una URL https://….trycloudflare.com
docker compose --profile publico up --build web-publico     # lo mismo con Docker
```

`docker compose up` a secas **no** publica nada: la web queda solo en
<http://localhost:8000>. Publicadla solo cuando haga falta (en clase, para el rival).

El servidor sigue en tu ordenador; Cloudflare solo reenvía las visitas. La URL
cambia cada vez y funciona mientras el comando esté abierto. **Con `--tunel` el
servidor no arranca con un PIN débil** (menos de 8 caracteres, `1234`, `password`…).
Si no pasas PIN, se genera uno aleatorio y se imprime.

### 2.4 La línea de comandos (lo que usará el profesor y el rival)

Todas las respuestas son **una línea JSON**. Sin `--config` se usa la **misma base
de datos que la web** (`data/lens/bd.csv`, las 50 personas del Dataset).

```bash
python -m biometria bd                                       # estado de la BD, ids exactos y protecciones activas
python -m biometria verificar   --id "Keanu Reeves" --foto foto.jpg
python -m biometria identificar --foto foto.jpg
python -m biometria identificar_sd --foto cara_recortada.png

# Métodos _t: el template tiene que haberlo generado el propio sistema
python -m biometria extraer --foto foto.jpg --salida t.npy   # crea t.npy y t.npy.sig
python -m biometria verificar_t --id "Keanu Reeves" --template t.npy
```

Con Docker, lo mismo dentro del contenedor (con la web levantada):

```bash
docker compose exec web python -m biometria verificar --id "Keanu Reeves" --foto Dataset/keanu_reeves_2021.png
```

Al buscar un id **no importan las mayúsculas, las tildes ni los espacios de más**:
`"lionel messi"` es `Lionel Messi` y `"Nicolás Maduro"` es `Nicolas Maduro`. Lo que sí
importa son las letras: algunos ids tienen erratas que vienen de los nombres de
fichero del Dataset (`Arianna Grande`, `Serenna Williams`, `Wiliem Dafoe`…) y hay que
escribirlas como salen en `bd`.

---

## 3. Cómo reconoce una cara

Cuando le das una foto, pasa por esta cadena:

```
foto ─► comprobar imagen ─► detectar cara ─► control de calidad ─► alinear ─► ArcFace ─► comparar ─► decisión
        (tamaño, formato)   (YuNet)          (tamaño, nitidez)     (5 puntos)  (512 números)  (coseno)   (umbral)
```

1. **Comprobar la imagen.** Máximo 10 MB y 25 megapíxeles, para que nadie tumbe el
   sistema con una imagen gigante o "bomba" de descompresión.
2. **Detectar la cara con YuNet**, una red neuronal pequeña de OpenCV. Devuelve
   dónde está la cara y **5 puntos**: los dos ojos, la nariz y las dos comisuras
   de la boca. Si no hay cara, `sin_cara`; si hay varias, error (o se coge la
   mayor, según la configuración).
3. **Control de calidad.** La cara debe medir al menos 60 píxeles y no estar
   borrosa (se mide con la varianza del Laplaciano: una foto movida tiene pocos
   bordes nítidos).
4. **Alinear.** Con los 5 puntos se gira y escala la cara para que los ojos, la
   nariz y la boca queden **siempre en la misma posición** en una imagen de
   112×112. Así el modelo compara caras, no posturas.
5. **ArcFace** (ResNet-50 entrenada con millones de caras) convierte la cara en
   un **vector de 512 números**, el *template*. Dos fotos de la misma persona dan
   vectores que apuntan en una dirección parecida; dos personas distintas, en
   direcciones casi perpendiculares. Se calcula también con la cara volteada en
   espejo y se suman, lo que lo hace más estable.
6. **Comparar** con la **similitud coseno** (el coseno del ángulo entre los dos
   vectores): 1 es idéntico y 0 no se parece en nada. Si una persona tiene varias
   fotos inscritas, cuenta la más parecida.
7. **Decidir con un umbral:**
   - **Verificar**: se acepta si la similitud llega a **0,35**.
   - **Identificar**: se compara con todos y gana el más parecido, si llega a
     **0,40** (más estricto porque se compara con muchos a la vez) y saca al
     segundo al menos **0,10** de ventaja. Si no, "desconocido".

### Los 9 métodos

| Método | Qué recibe | Qué se salta |
|---|---|---|
| `inscribir`, `verificar`, `identificar` | una foto | nada: cadena completa |
| `..._sd` (*sin detector*) | una cara ya recortada | detección y alineado (sí comprueba que sea una cara) |
| `..._t` (*template*) | el vector de 512 números | todo menos la comparación (exige la firma del sistema) |

### Por qué estos umbrales

No están puestos a ojo: se calibraron con **LFW**, una base pública de caras de
famosos, con `scripts/evaluar.py`. Comparando 1173 parejas de la misma persona y
615 432 de personas distintas:

- **EER 0,68 %**: el punto en el que se equivoca igual aceptando a un impostor
  que rechazando al de verdad. Cuanto más bajo, mejor.
- Con el umbral de verificación (0,35): acepta a un impostor el **0,0005 %** de
  las veces (FAR) y rechaza al de verdad el **0,85 %** (FRR).
- Identificación: acierta a la primera el **99,2 %** de las veces (*rank-1*).

---

## 4. Qué aporta el proyecto

- **Los 9 métodos del enunciado**, con errores claros y siempre el mismo formato
  (`{"error": "codigo", "mensaje": "…"}`), para que el profesor y el rival puedan
  usarlo sin leer el código.
- **Base de datos CSV protegida**: cifrada, firmada fila a fila y con sello contra
  copias antiguas (ver sección 5).
- **JumeX Lens**, la web para la demo:
  - Verificar, identificar e inscribir subiendo una foto o **con la cámara**.
  - Enseña cada paso del proceso: cara detectada, cara alineada, el vector, tiempos.
  - Identifica **a varias personas** en una misma foto.
  - Ficha de cada persona con sus fotos, editable (renombrar, borrar, foto principal).
  - **Dos roles**: *usuario* (verificar e identificar, sin ver la base de datos) y
    *administrador* con PIN (todo).
- **Evaluación medida**: `scripts/evaluar.py` (aciertos y errores en LFW) y
  `scripts/medir_defensas.py` (cuánto cuesta y cuánto protege cada defensa).
- **262 tests automáticos** que lanzan los ataques de verdad y comprueban que se
  paran sin estropear el uso normal.
- **Dockerfile** para ejecutarlo sin instalar nada.

---

## 5. Cómo nos hemos reforzado frente a ataques

El enunciado dice que el otro equipo nos va a atacar suponiendo que puede
**saltarse partes** del sistema, **leer** la base de datos o **escribir** en ella.
Hemos pensado como ese atacante, probado cada ataque y puesto una defensa.

**El principio que lo sostiene todo:** la **clave maestra** vive en `~/.jumex/`,
**fuera** de la carpeta `data/` y del repositorio. Quien lea o modifique `data/`
no tiene la clave, y sin la clave no puede firmar, cifrar ni engañar al sello.

**Ojo, dos escenarios** (detalle en `THREAT_MODEL.md`): si el rival ataca **nuestra**
web o un ordenador nuestro, eso vale. Pero si el rival ejecuta el sistema **en su
ordenador** (`docker compose up`), la clave se crea allí y es suya: el cifrado, la
firma y el sello no le frenan. Ahí solo cuentan las defensas que no dependen de un
secreto: coherencia y ancla de las muestras, margen de gemelos, cara en `_sd` y
validación de entradas. Lo decimos nosotros antes que el rival.

### 5.1 Resumen

| # | Ataque | Qué pasaba antes | Defensa |
|---|---|---|---|
| 1 | Añadir **mi cara** como foto extra de la víctima, o llegar a ella con **morphs** | Me aceptaba como ella para siempre (los morphs, 8 de 8 parejas) | Una foto nueva tiene que parecerse a la **primera** de esa persona, y las fotos extra no cuentan para quien no se parece a la primera |
| R | Usar la web para inscribir, borrar o ver la BD | Cualquiera con la URL podía hacerlo todo | Rol usuario por defecto; administrador con PIN, comprobado en el servidor |
| 2 | Pasar a `_t` un vector **copiado de la BD** o inventado | Se aceptaba (similitud 1,0) | El sistema **firma** sus vectores y `_t` rechaza los que no están firmados |
| 3 | **Ajustar** un vector poco a poco mirando la puntuación | Colaba en unos cientos de intentos (menos de 1 s) | Cualquier retoque rompe la firma; el redondeo y el límite de intentos solo lo retrasan |
| 4 | **Gemelos** o dobles en identificar | Elegía a uno aunque estuvieran empatados | Si el 1.º no le saca 0,10 al 2.º: "desconocido (ambiguo)" |
| 5 | Pasar a `_sd` (o a la cara marcada a mano en la web) algo que **no es una cara** | Aceptaba ruido o cualquier imagen | YuNet comprueba que hay una cara |
| 6a | **Leer** el CSV | Se veían los vectores | Cifrado AES-256-GCM |
| 6b | **Escribir** el CSV y volver a firmarlo | La clave estaba al lado del CSV | La clave está en `~/.jumex/` |
| 6c | Volver a una **copia antigua** del CSV o borrarlo (aunque se borre también el sello) | Pasaba sin aviso | Sello anti-rollback junto a la clave; sin sello, error (`sellar` lo acepta a propósito) |
| 1b | Inscribir la cara de alguien **con otro nombre** para que su identificación falle | Salía "ambiguo" | Al crear una persona nueva, si se parece ≥ 0,60 a alguien: `cara_ya_inscrita` |
| 6d | Dos procesos escribiendo a la vez | Se podía perder una inscripción | Bloqueo de fichero |
| 8 | Adivinar el PIN, saturar la web, robar la sesión, averiguar quién está inscrito | Sin límites | Bloqueo del PIN, límites por IP, timeouts, sesión que caduca, respuesta uniforme |

### 5.2 Cada defensa explicada

**1 · Envenenar la inscripción.** Como cuenta la foto más parecida de cada persona,
basta con colar una foto mía bajo el nombre de la víctima para pasar por ella
siempre. Al principio la comparábamos con la más parecida de **todas** las fotos de
esa persona (umbral 0,20), y la revisión lo rompió con **morphs**: 8 mezclas
progresivas de su cara con la mía, cada una parecida a la anterior, y al final yo
entraba como ella (8 de 8 parejas). Ahora hay dos defensas:
- **Coherencia contra la primera foto:** una foto nueva de alguien que ya existe
  tiene que parecerse a su **primera** foto (la inscripción original) tanto como para
  verificarse (0,35). La cadena de morphs se corta en cuanto se aleja de ella.
- **Ancla:** al verificar o identificar, las fotos extra solo cuentan si la foto se
  parece al menos 0,20 a la **primera**. Un morph que sí entró se parece a ella y a
  mí, pero yo contra su primera foto saco 0,00–0,06, así que no me sirve.
Error: `muestra_incoherente`.

**R · Roles en la web.** Por defecto eres *usuario*: verificas e identificas, ves el
nombre y el porcentaje, pero **no ves las fotos de la base de datos ni la lista de
parecidos**. Así no se puede recorrer la base de datos ni sacar de ahí la foto de
nadie. El *administrador* entra con PIN (la pastilla de arriba a la derecha). Lo
importante: el permiso se comprueba **en el servidor**. Aunque alguien llame a la
API a mano, sin sesión recibe `403 requiere_admin`.

**2 · Firma de templates.** Los métodos `_t` reciben directamente el vector de 512
números. Antes, alguien que copiara el vector de la víctima del CSV pasaba con
similitud 1,0. Ahora el sistema **firma con HMAC** cada vector que genera (es como
un sello que solo se puede poner con nuestra clave). `extraer` guarda la firma en
un fichero `.sig` junto al vector, y `_t` **rechaza** lo que no traiga una firma
válida (`template_no_firmado`). Un vector copiado de la BD no tiene firma, uno
inventado tampoco, y uno retocado la pierde.

**3 · Ajuste por la puntuación (hill-climbing).** El atacante parte de ruido,
cambia un poco el vector, mira si la puntuación sube, se queda con el cambio si
sube y repite. Lo medimos: **colaba en unos cientos de intentos** (216 sin
redondeo, 393 con 2 decimales, en menos de un segundo). Lo que lo para de verdad es
la firma: el primer retoque ya no está firmado y el sistema lo rechaza **sin dar
puntuación**, así que el atacante no tiene nada que le guíe. Además, la puntuación
de `_t` se redondea a 2 decimales y hay un límite de 50 intentos por minuto
(este límite solo funciona dentro de la web, que siempre está encendida; en la
línea de comandos cada ejecución empieza de cero). Por eso la defensa principal
es la firma.

**4 · Gemelos y dobles.** Si dos personas de la BD se parecen muchísimo, una foto de
una puede salir como la otra. Ahora, si el primer candidato no le saca al menos
**0,10** al segundo, el sistema dice "no se puede decidir" y no identifica a nadie.
Medido en LFW: **no se pierde ni un acierto**, porque entre personas distintas la
diferencia es siempre mucho mayor.

**5 · Cara en `_sd`.** Los `_sd` se saltan el detector, así que aceptaban cualquier
imagen nítida: ruido, un dibujo, un patrón fabricado. Ahora YuNet comprueba que
hay una cara (sin usarla para alinear, porque el método sigue siendo "sin
detector"). Lo mismo en la web cuando se marca la cara a mano y no se encuentran
sus puntos: antes ese recuadro se usaba tal cual y por ahí entraba ruido. Medido: acepta el **100 %** de las caras recortadas legítimas y rechaza
el **100 %** del ruido y el **99 %** de los trozos de fondo.

**6 · La base de datos.** Cuatro capas:
- **Firma por fila** (ya existía): cada fila lleva un HMAC que incluye su posición
  y el total de filas. Cambiar, borrar, duplicar o reordenar filas da `integridad_bd`.
- **Clave aparte**: antes estaba en `data/`, al lado del CSV. Quien podía escribir
  el CSV podía leer la clave y volver a firmarlo todo. Ahora está en `~/.jumex/`
  con permisos solo para el dueño.
- **Cifrado**: los vectores van cifrados con AES-256-GCM. El nombre de la persona
  forma parte del cifrado, así que no se puede mover el vector de una persona a otra.
- **Sello anti-rollback**: una copia antigua del CSV tiene firmas correctas (las
  hicimos nosotros), así que pasaba. Ahora, tras cada escritura, se guarda junto a
  la clave una huella del CSV. Si el CSV no coincide, da "rollback"; si ha
  desaparecido, también se avisa. Lo probamos con la BD real restaurando la copia
  de antes de cifrarla: detectado.
- **Bloqueo**: si dos procesos inscriben a la vez, el segundo espera al primero.

**8 · La web.**
- **PIN**: 5 fallos desde una IP en 15 minutos bloquean esa IP; 20 fallos en total
  bloquean el login para todos (por si el atacante cambia de IP). Durante el
  bloqueo ni el PIN correcto funciona.
- **PIN débil**: con `--tunel` no arranca con `1234` y similares.
- **Sesión**: caduca a las 2 horas; la cookie no es accesible desde JavaScript y,
  por el túnel, solo viaja por HTTPS.
- **Saturación**: como mucho 60 fotos por minuto y por IP (procesar una foto cuesta
  CPU), conexiones que se cierran a los 20 segundos si no mandan nada y un máximo de
  32 a la vez.
- **Averiguar quién está inscrito**: antes, verificar un nombre inventado decía "no
  está inscrito", así que se podía ir probando nombres. Ahora un usuario recibe un
  rechazo normal, igual que con alguien que sí existe.
- **Lo que ve un usuario**: el veredicto y el nombre, pero no los valores del
  vector ni cuánta gente hay en la base de datos.
- Ya estaba de antes: solo escucha en el propio ordenador, cabeceras contra CSRF,
  política de contenido estricta, límites de tamaño de foto.

### 5.3 Lo que NO podemos parar (y lo decimos nosotros)

El objetivo no es un sistema perfecto, que no existe, sino que **el rival no
encuentre ningún fallo que no conozcamos**. Estos son conocidos y están
documentados en `THREAT_MODEL.md`:

1. **El gemelo que no está inscrito.** El Dataset trae gemelos a propósito:
   Aitana (gemela de Melody), James Phelps (gemelo de Oliver) y Mary-Kate Olsen
   (gemela de Ashley). Una foto del gemelo que **no** está en la BD probablemente
   pase por su hermano: para ArcFace son casi la misma cara. El margen solo
   ayuda si los dos están inscritos. Es lo más probable que intente el rival.
2. **Foto de una foto, pantalla, vídeo o máscara.** ArcFace reconoce *quién* es,
   no *si está vivo*. No tenemos detección de vivacidad.
3. **Deepfakes, face swap y morphing** (mezclar dos caras): para el sistema, una
   imagen generada con tu cara eres tú.
4. **Ataques adversarios**: perturbaciones invisibles o gafas impresas diseñadas
   contra ArcFace, que es un modelo público.
5. **Rechazos de la persona real** por cambios de aspecto: fotos con décadas de
   diferencia (el Dataset tiene fotos de 1945, 1982 o 1985), transición de género,
   cirugía, maquillaje intenso.
6. **Quien tenga la clave o el PIN** puede hacerlo todo: son la raíz de confianza.
7. **La primera foto de una persona** no se compara con nada: si el administrador
   la inscribe con una foto equivocada, el sistema se la cree.
8. **Las fotos de inscripción están en el repositorio** (`Dataset/`, para que
   `docker compose up` funcione solo): con la foto exacta de alguien, verificar da
   1,00. Son fotos públicas de famosos y el repo es privado, pero el rival lo tiene.
9. **El rival ejecutando el sistema en su ordenador** tiene la clave (ver arriba).
10. **Gemelos al inscribir**: la defensa contra la misma cara con otro nombre puede
    rechazar al gemelo de alguien inscrito. El admin la desactiva un momento para
    inscribirlo.

---

## 6. Qué contestar si os preguntan

- **"¿Por qué ArcFace?"** Nos lo asignaron, y además es el estado del arte en
  reconocimiento facial abierto: EER 0,68 % y 99,2 % de acierto a la primera en LFW,
  frente a un EER del ~37 % y un 11 % de acierto de LBP (el método clásico que
  teníamos al principio).
- **"¿Los umbrales de dónde salen?"** De medirlos en LFW con `scripts/evaluar.py`,
  eligiendo un FAR muy bajo (0,0005 %) a cambio de rechazar algo menos del 1 % de
  las fotos legítimas.
- **"¿Los métodos `_t` funcionan si rechazan templates?"** Sí, con cualquier
  template generado por el sistema (`extraer` lo firma). Un template de otro
  sistema no serviría de todos modos: cada sistema produce vectores distintos. Si
  hiciera falta aceptar cualquiera: `exigir_firma_t: false` en la configuración.
- **"¿Y si el atacante lee la base de datos?"** Ve vectores cifrados. Aunque los
  descifrara, no podría usarlos en `_t` porque no están firmados.
- **"¿Y si la modifica?"** La firma por fila lo detecta; si pone una copia antigua,
  el sello; si la borra, también el sello; y si además borra el sello, el sistema
  se niega a usar la BD hasta que el admin la acepte con `sellar`. Para falsificarla
  necesita la clave, que no está en `data/`.
- **"¿Y si ejecutáis el sistema en vuestro ordenador?"** Entonces la clave es
  vuestra y el cifrado, la firma y el sello no os frenan: lo explicamos en
  `THREAT_MODEL.md`, "Dos escenarios". Ahí solo cuentan las defensas que no dependen
  de un secreto (coherencia y ancla de las muestras, duplicados, margen de gemelos,
  cara en `_sd`, validación de entradas).
- **"¿Por qué 50 personas y no 51?"** El docente aclaró que eran sus 49 fotos más
  una elegida por cada equipo. La nuestra es Lionel Messi.
- **"¿Cuánto acierta con vuestras fotos?"** Con la foto de inscripción, en JPEG de
  baja calidad, en espejo, girada, con otra luz o en gris, siempre por encima de
  0,77; con los ojos tapados, 0,48 o más. Entre las 50 personas, el impostor más
  parecido da 0,25. Todo en `docs/EVALUACION.md`.
- **"¿Qué pasa con los gemelos?"** Ver 5.3, punto 1: lo sabemos, lo medimos y
  explicamos por qué no tiene solución con este tipo de sistema.
- **"¿Cómo sé qué protecciones están activas?"**
  `python -m biometria bd` las lista (en Docker: `docker compose exec web python -m biometria bd`).

---

## 7. Dónde está cada cosa

```
biometria/
  sistema.py          los 9 métodos y las defensas de los métodos (firma, margen, coherencia, ancla)
  detector.py         YuNet, alineado, calidad, comprobación de cara en _sd
  extractores/        arcface.py (el principal), sface.py, lbp.py
  bd.py               CSV: firmas, cifrado, sello anti-rollback, bloqueo
  config.py           carga y valida config.yaml
  errores.py          todos los errores con su código
  cli.py              línea de comandos (salida JSON)
  web/servidor.py     JumeX Lens: API, roles, PIN, límites
  web/static/         la interfaz (HTML, CSS, JavaScript)
config.yaml           configuración única: CLI, Docker y web (BD en data/lens/bd.csv)
config_lens.yaml      igual que config.yaml (se mantiene por compatibilidad)
scripts/              preparar_dataset, inscribir_csv, evaluar, medir_defensas, proteger_bd…
tests/                262 tests (test_defensas.py y test_web.py prueban los ataques)
THREAT_MODEL.md       cada ataque y defensa en detalle, con sus tests
~/.jumex/             (fuera del repo) claves maestras y sellos. NO compartir
```

Todas las defensas se pueden encender o apagar en la sección `defensa:` y `bd:`
de la configuración, por ejemplo para medir el sistema sin ellas.

---

## 8. Problemas típicos

| Síntoma | Causa y solución |
|---|---|
| `No module named yaml` (u otro) | No está activado el entorno: `source ~/.venvs/jumex/bin/activate` |
| `integridad_bd` sin haber tocado nada | Falta la clave en `~/.jumex/` o no es la que firmó esa BD. Si venías de una versión anterior, ejecuta `scripts/proteger_bd.py` (sección 2.2) |
| `integridad_bd` con "rollback" | El CSV no es el último que escribió el sistema: alguien lo ha sustituido por una copia |
| `template_no_firmado` | El template no lo generó este sistema, o falta el `.sig` al lado. Genéralo con `extraer` |
| La web dice "BD vacía" | No se ha inscrito el Dataset (sección 2.1, paso 5) |
| `--tunel` no arranca | PIN débil (usa 8+ caracteres) o falta `cloudflared` (`brew install cloudflared`) |
| `Operation timed out` en git | El repo está dentro de OneDrive/iCloud. Clónalo en `~/dev` |
| `pin_bloqueado` | Demasiados PIN incorrectos: espera 15 minutos (o reinicia el servidor) |

**Antes de subir nada a git:** `git status` no debe mostrar `data/`, `Dataset/`,
`models/`, ficheros `.key`, `.csv` ni comprimidos. Si aparece alguno, no lo añadas.
