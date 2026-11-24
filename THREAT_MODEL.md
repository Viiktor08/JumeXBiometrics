# Modelo de amenazas de JumeXBiometrics

Este documento recoge **cómo se puede atacar el sistema y qué hace para
defenderse**. Responde a lo que pide el enunciado: evaluar el sistema
*suponiendo que el atacante se salta alguna parte, lee la base de datos o
escribe en ella*. El objetivo no es un sistema perfecto (imposible), sino que
**no queden agujeros que nosotros no conozcamos**. Por eso hay un apartado final
con lo que **no** paramos y por qué.

Cada defensa trae **el ataque que para**, **qué le pasaba al sistema sin ella** y
**cómo se comprueba**. Todas tienen tests en `tests/test_defensas.py` y
`tests/test_web.py`, y los umbrales se han medido con LFW:
`scripts/evaluar.py` (FAR/FRR) y `scripts/medir_defensas.py` (coste de cada defensa).

## El atacante y lo que puede hacer

Suponemos un atacante con las capacidades que da el enunciado, de menor a mayor:

1. **Usuario normal**: manda fotos a `inscribir`/`verificar`/`identificar` (CLI,
   web o API) y ve la respuesta, **incluida la puntuación**.
2. **Se salta partes**: usa `*_sd` (sin detector) y `*_t` (con un template que
   él fabrica). El enunciado obliga a ofrecer estos métodos.
3. **Lee la base de datos**: tiene el `bd.csv`.
4. **Escribe en la base de datos**: puede modificar, borrar o sustituir el `bd.csv`.
5. **Está presente**: pone una foto, una pantalla o una máscara ante la cámara.

Lo que **no** suponemos: que ejecute código dentro de nuestro proceso ni que lea
la **clave maestra**. Por eso la clave vive **fuera** de la carpeta de datos
(`~/.jumex/`, o la variable de entorno `BIOMETRIA_HMAC_KEY`): leer o escribir
`data/` no da la clave.

## Dos escenarios: ¿quién ejecuta el sistema?

La suposición anterior solo se cumple si el sistema corre en una máquina **nuestra**.
Para la evaluación del rival hay dos maneras de hacerlo, y no protegen lo mismo:

| | **1 · Rival remoto** (usa nuestra web o una máquina nuestra) | **2 · Rival operador** (`git clone` + `docker compose up` en su ordenador) |
|---|---|---|
| Clave maestra | Nuestra, en nuestro `~/.jumex/` | **Suya**: se genera aleatoria en su `~/.jumex/` |
| Cifrado, HMAC por fila, sello (paso 6) | ✅ Le impiden leer y manipular la BD | ❌ Descifra, vuelve a firmar filas y regenera el sello |
| Firma de templates en `_t` (pasos 2 y 3) | ✅ No puede fabricar ni ajustar templates | ❌ `SistemaBiometrico.firmar()` le firma lo que quiera |
| Roles y PIN de la web (paso R y 8) | ✅ | ❌ El PIN sale en su consola |
| Coherencia y ancla de las muestras (paso 1) | ✅ | ✅ Siguen valiendo **si usa los métodos** del sistema; si edita el código o la BD con la clave, no |
| Margen de gemelos (paso 4), cara en `_sd` (paso 5), validación de entradas | ✅ | ✅ Igual que arriba |

**Conclusión.** Las defensas criptográficas protegen el escenario 1. En el 2 el
rival es, a todos los efectos, el administrador: lo que se evalúa entonces es el
**reconocimiento** (FAR/FRR, gemelos, presentación, morphs, adversarios) y las
defensas que no dependen de un secreto. Proponemos al rival evaluar los dos: el
escenario 1 con nuestra web publicada (`docker compose --profile publico up
web-publico`) y el 2 con su propio clon.

## Las defensas, de un vistazo

Todas las contramedidas viven en las secciones `bd:` y `defensa:` de la
configuración y **se pueden desactivar**, para medir el sistema sin ellas.

| # | Ataque | Defensa | Estado |
|---|---|---|---|
| 1 | Envenenar la inscripción (añadir mi cara como muestra de la víctima, o una cadena de morphs) | `coherencia_muestra` contra la **primera** muestra + `ancla_muestra` al verificar e identificar | ✅ |
| R | Cualquiera inscribe, borra o lee la BD por la web | Dos roles: usuario (verificar/identificar) y admin con PIN | ✅ |
| 2 | Template fabricado o copiado de la BD en `_t` | Firma HMAC de los templates; `_t` **rechaza** los no firmados | ✅ |
| 3 | Hill-climbing con la puntuación | Firma obligatoria + redondeo + límite de intentos | ✅ |
| 4 | Gemelos y dobles en identificación | Margen mínimo entre el 1.º y el 2.º candidato | ✅ (parcial, ver riesgos) |
| 5 | `_sd` (o la cara marcada a mano en la web) con una imagen que no es una cara | YuNet comprueba que hay una cara (sin alinear) | ✅ |
| 6 | Leer la BD, escribirla con la clave al lado, rollback, escrituras a la vez | Clave aparte, cifrado AES-GCM, sello anti-rollback, bloqueo de fichero | ✅ |
| 7 | Foto de una foto, pantalla, máscara (presentación), o la foto exacta de `Dataset/` | Sin vivacidad: riesgo aceptado y documentado | ⚠️ |
| 8 | Fuerza bruta del PIN, saturar el servidor, robar la sesión, enumerar nombres | Bloqueo del PIN, sesión que caduca, límites por IP, timeouts, respuesta uniforme | ✅ |

`python -m biometria bd` enseña qué protecciones están activas.

---

## Paso 1 · Envenenar la inscripción

**El ataque.** El atacante consigue añadir **su** cara como una muestra más de la
víctima (en la web, el botón "Añadir foto" de una ficha). A partir de ese
momento la base de datos tiene, bajo el nombre de la víctima, un template que es
el del atacante.

**Qué pasaba sin defensa.** Como la puntuación frente a una persona es el
**máximo** sobre sus muestras (`bd.agregacion: max`), basta una muestra
envenenada para que el atacante **verifique y se identifique como la víctima**
para siempre.

**La primera defensa, y cómo cayó.** Al principio, la muestra nueva tenía que
parecerse al menos 0,20 a la **más parecida** de las que ya tenía la persona. Eso
frena una cara ajena directa, pero la revisión del 23/11 lo rompió con una
**deriva de morphs**: 8 mezclas progresivas víctima → atacante (fundido de las dos
caras alineadas, del 12 % al 96 %), cada una parecida a la anterior. Se aceptaron
**las 8 en las 8 parejas probadas**, y después otra foto del atacante se verificaba
como la víctima con 0,92–0,98 (antes 0,00–0,06), sin que la víctima notara nada.

**Las defensas actuales.**

1. **Coherencia contra la primera muestra** (`defensa.coherencia_muestra: 0.35`).
   Al **añadir** una muestra a una persona que **ya existe**, el nuevo template
   tiene que parecerse a su **primera** muestra (la inscripción original), no a la
   más parecida, y tanto como para verificarse (mismo umbral que verificación; en
   LFW solo el 0,22–0,85 % de los pares genuinos queda por debajo). La cadena de
   morphs se corta en cuanto se aleja de la original. Si no, `muestra_incoherente`.
   Está en `SistemaBiometrico._inscribir`, así que cubre `inscribir`,
   `inscribir_sd`, `inscribir_t` y la web. No se aplica a la primera muestra de una
   persona nueva: no hay con qué comparar.
2. **Ancla al puntuar** (`defensa.ancla_muestra: 0.20`). La coherencia sola no basta:
   un morph al ~70 % se parece a la víctima lo bastante para entrar (≈0,4) y al
   atacante casi como él mismo (≈0,9), y verificar usa la muestra más parecida. Por
   eso, al verificar o identificar, **las muestras extra solo cuentan si la foto se
   parece al menos 0,20 a la primera muestra**; si no, la puntuación es la de la
   primera. Al atacante (0,00–0,06 contra la original) no le sirve ningún morph; a
   la persona real, sus fotos extra le siguen ayudando (fotos de otra época, otra
   luz). En LFW, solo el 0,12 % de los impostores llega a 0,20 con la víctima.

**Límite conocido.** No para a quien envenena con una cara que **de verdad se
parece** a la víctima (un gemelo, un morph que ya supera 0,35 con la original), ni
a quien edita la BD con la clave (escenario 2). La primera muestra de cada persona
es la raíz de confianza.

**Cómo se comprueba.** `test_defensas.py::test_coherencia_bloquea_envenenamiento`,
`::test_coherencia_desactivada_deja_pasar_el_ataque`,
`::test_coherencia_compara_con_la_primera_muestra`,
`::test_deriva_con_morphs_no_cuela_al_atacante` y
`::test_sin_ancla_la_deriva_si_cuela_al_atacante` (prueba de que es el ancla la
que lo para).

---

## Paso de roles · Quién puede ver y tocar la base de datos

**El ataque.** Publicada con `--tunel`, la web daba a **cualquiera** con la URL
el control total: inscribir, borrar, renombrar y ver toda la base de datos con
fotos y porcentajes de parecido.

**La defensa: dos roles.**

- **Usuario** (por defecto, sin PIN): **verifica** e **identifica**. Ve el
  veredicto, el nombre y el porcentaje, pero **no la foto guardada ni la lista de
  parecidos**, ni los valores del template, ni cuánta gente hay inscrita (tampoco
  en las respuestas de verificar e identificar, ni en las de error). No puede
  inscribir, borrar ni abrir "Base de datos", y `GET /api/estado` no le dice cuánta
  gente hay ni con qué umbrales.
- **Administrador** (con PIN): además inscribe, edita, borra y ve la BD.

El rol se comprueba **en el servidor, en cada endpoint**: sin sesión,
`POST /api/inscribir`, `GET /api/personas` o `DELETE /api/personas` devuelven
**403 `requiere_admin`**. Ocultar botones no es la defensa. La sesión va en una
cookie `HttpOnly; SameSite=Strict` y la comparación del PIN es en tiempo
constante. El paso 8 añade el bloqueo de intentos y la caducidad.

**Cómo se comprueba.** `test_web.py::test_login_logout_y_pin_incorrecto`,
`::test_usuario_no_puede_ver_ni_editar_la_bd`,
`::test_usuario_identifica_pero_sin_foto_ni_candidatos`.

---

## Paso 2 · Template fabricado o copiado en `_t`

**El ataque.** `inscribir_t`, `verificar_t` e `identificar_t` reciben directamente
un **template**, saltándose detector y extractor. Quien lea la BD (sin cifrar)
copia el template de la víctima y lo pasa por `verificar_t`: similitud 1,000,
aceptado. Sin leerla, puede fabricar un vector a medida.

**Qué pasaba sin defensa.** Del template solo se validaba la forma (dimensión,
norma, valores finitos). No había forma de distinguir uno nuestro de uno inventado.

**La defensa: firma obligatoria.**

- El sistema **firma con HMAC-SHA256** cada template que genera, con una clave
  derivada de la maestra (uso `template-sig`). Solo quien tiene la clave firma.
- `python -m biometria extraer --foto f.jpg --salida t.npy` crea `t.npy` **y**
  `t.npy.sig`. Los `_t` leen el `.sig` de al lado (o `--firma`).
- Con `defensa.exigir_firma_t: true` (activado), los tres `_t` **rechazan** con
  `template_no_firmado` todo template sin firma válida. Eso incluye el fabricado,
  el **copiado de la BD** (en la BD no hay firmas de template) y cualquier
  template **retocado**: cambiar un solo valor invalida la firma.
- La respuesta incluye `fiable: true` cuando la firma es válida. Con
  `exigir_firma_t: false` el sistema vuelve a aceptar todo y solo avisa con
  `fiable: false/null`.

**Por qué no rompe el enunciado.** Los métodos `_t` siguen existiendo y
funcionando con cualquier template que haya generado el sistema. Un template de
otro sistema no serviría de todos modos: cada pipeline (detector, alineado,
modelo) produce vectores distintos.

**Qué NO resuelve.** Quien tenga la clave maestra firma lo que quiera. Por eso
la clave va aparte (paso 6).

**Cómo se comprueba.** `test_defensas.py::test_estricto_rechaza_template_sin_firma_o_con_firma_falsa`,
`::test_estricto_acepta_los_templates_propios`,
`::test_estricto_rechaza_el_template_copiado_de_la_bd`,
`::test_estricto_rechaza_un_template_firmado_y_retocado`,
`::test_la_firma_depende_de_la_clave`.

---

## Paso 3 · Hill-climbing con la puntuación

**El ataque.** El sistema **devuelve la puntuación** (hace falta para evaluarlo).
Un atacante parte de un vector cualquiera y, probándolo por `verificar_t`, se
queda con cada variación que **sube** la puntuación hasta superar el umbral.
Medido: partiendo de ruido, con perturbaciones aleatorias de tamaño ajustado,
supera 0,35 en **216 consultas** (393 con la puntuación redondeada a 2 decimales),
en menos de un segundo y sin saber nada de la víctima. (Una primera medición, con
pasos peores, daba 15 000–36 000: el ataque es bastante más barato de lo que
parecía.)

**Las defensas, y cuál para de verdad el ataque.**

1. **Firma obligatoria (paso 2).** Es la que lo cierra: cada variación del
   template pierde la firma y `verificar_t` la rechaza **antes** de compararla.
   El atacante no obtiene ninguna puntuación que lo guíe.
2. **Redondeo** a `decimales_puntuacion_t` (2) de la puntuación que sale por
   `_t`. La decisión se toma con la puntuación exacta. **Solo, no basta**: medido,
   con 2 decimales el ataque sigue funcionando (393 consultas en vez de 216).
3. **Límite de intentos**: `max_intentos_t` (50) por id cada `ventana_intentos_s`
   (60 s), error `demasiados_intentos` (429 en la web). Ojo: es **por proceso y
   en memoria**. Protege al servidor web, que vive mucho tiempo, pero **no a la
   CLI**: cada `python -m biometria verificar_t` es un proceso nuevo con el
   contador a cero. En la CLI quien para el ataque es la firma. Y aun en la web,
   393 consultas a 50 por minuto son unos 8 minutos: el límite solo lo retrasa.

**Si se desactiva la firma** (`exigir_firma_t: false`, por ejemplo para que el
profesor o el rival prueben templates de fuera), el hill-climbing vuelve a ser
trivial. En ese caso conviene no devolver la puntuación por `_t` o redondearla a
1 decimal.

**Lo que queda.** Un hill-climbing con **fotos** (modificar una imagen poco a
poco por `verificar`) no lo para la firma: cada foto es legítima. Necesita
muchas más consultas y genera imágenes visiblemente manipuladas; en la web lo
frena el límite por IP del paso 8. En la CLI, ver riesgos aceptados.

**Cómo se comprueba.** `test_defensas.py::test_estricto_rechaza_un_template_firmado_y_retocado`,
`::test_la_puntuacion_de_t_se_redondea`, `::test_limite_de_intentos_frena_el_barrido`,
`::test_las_operaciones_con_foto_no_gastan_el_cupo`.

---

## Paso 4 · Gemelos y dobles en identificación

**El ataque.** En identificación 1:N basta con que **alguien** supere el umbral.
Si en la BD hay dos personas muy parecidas (gemelos, dobles), una foto de una
puede salir como la otra, o una foto "a medio camino" (un morph de las dos) puede
identificarse como cualquiera.

**La defensa.** Con `defensa.margen_identificacion` (0,10), el 1.º candidato
tiene que sacarle al 2.º al menos 0,10. Si no, el resultado es **desconocido**
con `ambiguo: true` (la web dice "No se puede decidir"). Es más seguro no
identificar que identificar al gemelo equivocado.

**Por qué 0,10.** Medido con `scripts/medir_defensas.py` en LFW (291 personas en
la BD, 542 fotos de inscritos): con márgenes de 0 a 0,15 **no se pierde ni una
identificación correcta**. En personas distintas, el 1.º y el 2.º están muy
separados; solo se acercan con gemelos o morphs.

**Qué NO resuelve.** Si el gemelo **no está inscrito**, no hay 2.º candidato
cercano: su foto puede identificarse como su hermano (ver riesgos aceptados). La
mitigación es inscribir también al gemelo (aunque sea como "no autorizado"):
entonces el margen convierte la confusión en "ambiguo".

**Cómo se comprueba.** `test_defensas.py::test_margen_marca_ambiguo_un_empate_entre_dos_personas`.

---

## Paso 5 · `_sd` con una imagen que no es una cara

**El ataque.** Los métodos `_sd` se saltan el detector: aceptan **cualquier**
imagen nítida como si fuera una cara recortada. Se puede inscribir ruido,
un dibujo o un patrón adversario, y verificar con imágenes que nunca pasarían
por el pipeline completo.

**La defensa.** Con `defensa.cara_en_sd: true`, antes de extraer, YuNet comprueba
que la imagen contiene una cara: se le añade un borde (YuNet no ve bien una
cara que llena toda la imagen) y se pide una confianza de 0,5. **La detección no
se usa para alinear**: el método sigue siendo "sin detector", como pide el
enunciado. Si no hay cara: `sin_cara`.

**Medido** (`scripts/medir_defensas.py`, LFW):

| Entrada | Resultado |
|---|---|
| Recortes de cara legítimos (alineados y holgados) | **580/580 aceptados (100 %)** |
| Ruido, colores lisos y degradados | **600/600 rechazados (100 %)** |
| Trozos de fondo de fotos reales | 1062/1072 rechazados (99,1 %) |

**La misma comprobación en la web.** Al marcar la cara a mano (`?caja=x,y,w,h`), si
YuNet no encuentra sus puntos dentro del recuadro, este se usa sin alinear, como
en `_sd`. Antes eso se hacía **sin** comprobar que hubiera una cara: la revisión
mandó ruido con `caja=0,0,400,400` y llegó al extractor (puntuación 0,15 al
identificar). Ahora ese recorte pasa por la misma comprobación (`sin_cara`).

**Cómo se comprueba.** `test_defensas.py::test_sd_rechaza_lo_que_no_es_una_cara` y
`test_web.py::test_web_caja_manual` (recuadro sin cara y ruido con `caja`).

---

## Paso 6 · Leer y escribir la base de datos

**Los ataques.**
- **Leer** el `bd.csv`: los templates estaban en claro (base64), listos para
  copiarlos (paso 2) o estudiarlos.
- **Escribir** con la clave **al lado**: la clave estaba en `data/clave_hmac.key`,
  junto al CSV. Quien podía escribir la BD podía leer la clave y volver a firmar
  cualquier cambio: el HMAC no servía de nada.
- **Rollback**: volver a una copia antigua y **válida** del CSV (por ejemplo, con
  una persona que el admin ya borró). Cada fila tiene un HMAC correcto, así que
  pasaba.
- **Borrar** el CSV: equivalía a una BD vacía, sin aviso.
- **Dos procesos escribiendo a la vez**: los dos leían, añadían su muestra y
  reescribían; una de las muestras se perdía.

**Las defensas.**
1. **Clave aparte**: `bd.ruta_clave: ~/.jumex/clave_*.key` (permisos 0600), o la
   variable de entorno `BIOMETRIA_HMAC_KEY`. `python -m biometria bd` avisa
   (`clave_junto_a_bd`) si sigue al lado. `scripts/proteger_bd.py` la mueve.
2. **Cifrado** (`bd.cifrar_templates: true`): cada template va con AES-256-GCM y
   el id como dato asociado (no se puede mover una muestra a otra persona). Leer
   el CSV no da los vectores.
3. **Sello anti-rollback** (`bd.anti_rollback: true`): tras cada escritura se
   guarda, **junto a la clave**, el SHA-256 del CSV firmado con HMAC. Al leer, el
   CSV tiene que coincidir: una copia antigua da `integridad_bd` ("rollback") y
   un CSV borrado da "la base de datos ha desaparecido". El sello se escribe en
   dos fases (versión pendiente y luego confirmada), así quien lee a mitad de una
   escritura no ve un falso rollback.
4. **Bloqueo de fichero** (`flock`) en toda operación que lee, modifica y
   reescribe: dos procesos ya no se pisan.

Antes de reescribir siempre se verifica el fichero entero: nunca se vuelve a
firmar contenido manipulado.

**Qué NO resuelve.** Si se **borra el sello** (`~/.jumex/sello-*.json`) y se pone
una copia antigua de la BD, el sistema no tiene con qué compararla y se fía del CSV
que encuentra: medido, una BD de 48 personas sustituyó a la de 49 sin aviso. Hace
falta poder escribir en `~/.jumex/`, no la clave. Además, quien tenga la clave (o pueda escribir en `~/.jumex/`) puede
falsificarlo todo. En Windows no hay `flock` (la escritura sigue siendo atómica).
Si el proceso muere en mitad de una escritura, el sello admite dos versiones
hasta la siguiente escritura.

**Cómo se comprueba.** `test_defensas.py::test_rollback_a_una_copia_antigua_se_detecta`,
`::test_borrar_la_bd_entera_se_detecta`, `::test_el_sello_manipulado_se_detecta`,
`::test_lectura_a_mitad_de_escritura_no_da_falso_rollback`,
`::test_bd_cifrada_no_deja_leer_los_templates_y_la_clave_va_aparte`,
`::test_recodificar_cifra_una_bd_en_claro`,
`::test_dos_procesos_escribiendo_a_la_vez_no_pierden_muestras`, y en `test_bd.py`
la detección de filas cambiadas, borradas, duplicadas o reordenadas.

---

## Paso 7 · Ataques de presentación (foto, pantalla, máscara)

**El ataque.** Enseñar a la cámara una foto impresa, un móvil con la foto o un
vídeo de la víctima, o una máscara.

**Situación.** ArcFace compara **identidad**, no **vivacidad**: una buena foto
de la víctima da la misma puntuación que la víctima en persona. No tenemos
detección de vivacidad (PAD). Además, la práctica trabaja con **fotos** (el
dataset son fotos), así que cualquier comprobación de vivacidad solo tendría
sentido en la cámara de la web, y el atacante podría seguir subiendo una foto.

**Lo que sí hay.** Las fotos pasan controles de calidad (cara mínima, nitidez) y
el usuario de la web no ve las fotos de la BD. **Pero las fotos con las que se
inscribió a cada persona están en el repositorio** (`Dataset/`, para que `docker
compose up` cree la BD solo): con ellas, verificar da **1,00** para cualquiera de
los 49 (medido), y también pasan recomprimidas, en espejo, giradas, en gris o con
otra luz (siempre ≥ 0,77). **Es un riesgo aceptado**: ver abajo.

---

## Paso 8 · Endurecer la web

**Los ataques y sus defensas** (en `biometria/web/servidor.py`):

| Ataque | Antes | Ahora |
|---|---|---|
| Fuerza bruta del PIN | Intentos ilimitados | 5 fallos por IP en 15 min bloquean esa IP; 20 fallos en total bloquean el login para todos (por si el atacante cambia de IP). Durante el bloqueo, ni el PIN correcto entra |
| PIN débil (`1234`) | Aceptado | Con `--tunel` el servidor **no arranca** con un PIN de menos de 8 caracteres, común, repetido o secuencial. En local, aviso |
| Robar la cookie de sesión | La sesión no caducaba nunca | Caduca a las 2 h (`Max-Age` y control en el servidor); por el túnel lleva `Secure` |
| Saturar la CPU con fotos | Sin límite | 60 operaciones con foto por IP y minuto (`demasiadas_peticiones`, 429). El admin no tiene ese límite |
| Slowloris (abrir conexiones y no mandar nada) | Un hilo bloqueado para siempre | Timeout de socket de 20 s y como mucho 32 conexiones a la vez |
| Averiguar quién está inscrito con verificar | "no inscrito" para nombres que no existen | El usuario recibe un rechazo normal, con una puntuación de impostor estable para ese nombre. El admin sí ve el motivo real |

Con el túnel, la IP real llega en `CF-Connecting-IP` (Cloudflare la sobrescribe);
sin túnel, esa cabecera se ignora. Ya estaban de antes: CSP estricta, `Host` y
cabecera `X-JumeX-Lens` obligatorios (CSRF y DNS rebinding), límite de tamaño de
foto y de píxeles, y solo se escucha en 127.0.0.1.

**Cómo se comprueba.** `test_web.py::test_pin_bloqueado_tras_varios_fallos`,
`::test_bloqueo_por_ip_y_general`, `::test_la_sesion_caduca`,
`::test_cookie_con_caducidad`, `::test_limite_de_peticiones_por_ip`,
`::test_conexion_callada_se_cierra`, `::test_pin_debil`,
`::test_pin_debil_no_arranca_con_tunel`,
`::test_verificar_no_revela_quien_esta_inscrito`.

---

## Riesgos aceptados (lo que el sistema NO para)

Los decimos nosotros antes de que los encuentre el equipo rival.

1. **Gemelo idéntico no inscrito.** El Dataset incluye personas con gemelo
   idéntico (Aitana/Melody Etxebarria, James/Oliver Phelps, Mary-Kate/Ashley
   Olsen). Una foto del gemelo que **no** está en la BD puede verificarse o
   identificarse como su hermano: para ArcFace son casi la misma cara. El margen
   del paso 4 solo ayuda si los dos están inscritos.
2. **Ataques de presentación** (paso 7): foto, pantalla, vídeo o máscara de la
   víctima. Sin PAD no se distinguen.
3. **Deepfakes, face swap y morphing.** Una imagen generada con la cara de la
   víctima es, para el sistema, la víctima. Un morph de dos personas puede
   verificarse como las dos (el margen lo marca como ambiguo solo si las dos
   están inscritas y el morph cae en medio).
4. **Ejemplos adversarios** (perturbaciones invisibles, gafas o parches impresos)
   construidos contra ArcFace: el modelo es público.
5. **Cambios de aspecto legítimos** que hacen fallar a la persona real (FRR):
   fotos con décadas de diferencia (el Dataset tiene fotos de 1945, 1982 o 1985),
   transición de género, cirugía, maquillaje intenso, condiciones de la piel.
   No es una brecha, pero el rival lo puede usar para demostrar rechazos.
6. **Hill-climbing con fotos por la CLI**: la firma no lo para (cada foto es
   legítima) y el límite de intentos de la CLI no persiste entre procesos.
   Requiere muchas consultas y deja imágenes manipuladas.
7. **Quien tiene la clave maestra o el PIN de admin** puede hacerlo todo: es la
   raíz de confianza.
8. **La primera muestra de una persona** no se comprueba con nada: si el admin
   inscribe a alguien con la foto equivocada, el sistema la cree.
9. **Las fotos de inscripción están en el repositorio** (`Dataset/`). Se decidió así
   para que `docker compose up` cree la BD sin pasos manuales; el repositorio es
   privado y son fotos públicas de famosos, pero el rival las tiene. Con la foto
   exacta de una persona, `verificar` da **1,00** (medido con las 49), y las
   variantes de esa foto también pasan. Mitigación posible, no implementada:
   rechazar las comparaciones casi idénticas (≥ 0,97–0,99) a una muestra guardada.
10. **El rival como operador** (ver "Dos escenarios"): si ejecuta el sistema en su
    ordenador, la clave es suya y las defensas de los pasos 2, 3, 6 y 8 no le afectan.
11. **Borrar el sello** y restaurar una copia antigua de la BD (paso 6): sin sello,
    se confía en el CSV actual. Hace falta escribir en `~/.jumex/`.
12. **La misma cara con otro nombre.** La coherencia solo mira las muestras de la
    **misma** persona. Si alguien inscribe la foto de la víctima con otro nombre (el
    admin, o cualquiera con `--inscripcion-abierta`), identificar a la víctima da
    `ambiguo` → desconocido (medido: `id: null, ambiguo: true`); verificar sigue
    funcionando. Mitigación posible, no implementada: al crear un id nuevo,
    identificar antes y rechazar si alguien supera el umbral.
13. **Ids exactos.** Los ids distinguen mayúsculas y salen del nombre de fichero del
    Dataset, con sus erratas (`Arianna Grande`, `Serenna Williams`…): verificar
    con el nombre bien escrito da un rechazo que no es culpa del reconocimiento.
