# Modelo de amenazas de JumeXBiometrics

Este documento recoge **cómo se puede atacar el sistema y qué hace para
defenderse**. Responde a lo que pide el enunciado: evaluar el sistema
*suponiendo que el atacante se salta alguna parte, lee la base de datos o
escribe en ella*. El objetivo no es un sistema perfecto (imposible), sino que
**no queden agujeros que nosotros no conozcamos**.

Cada defensa trae **el ataque que para**, **qué le pasaba al sistema sin ella** y
**cómo se comprueba**. Los ataques se pueden lanzar de verdad con
[`scripts/atacar.py`](scripts/atacar.py), que monta una base de datos temporal
(nunca toca la real) y dice de cada uno si tiene éxito o queda bloqueado.

## El atacante y lo que puede hacer

Suponemos un atacante con las capacidades que da el enunciado, de menor a mayor:

1. **Usuario normal**: manda fotos a `inscribir`/`verificar`/`identificar` (CLI,
   web o API) y ve la respuesta, **incluida la puntuación**.
2. **Se salta partes**: usa `*_sd` (sin detector) y `*_t` (con un template que
   él fabrica). El enunciado obliga a ofrecer estos métodos.
3. **Lee la base de datos**: tiene el `bd.csv` (y quizá la clave, si está en el
   mismo sitio).
4. **Escribe en la base de datos**: puede modificar el `bd.csv`.
5. **Está presente**: pone una foto, una pantalla o una máscara ante la cámara.

Lo que **no** suponemos: que ejecute código dentro de nuestro proceso, ni que
tenga nuestra clave HMAC si la guardamos aparte.

## Las defensas, de un vistazo

Todas las contramedidas de software viven en la sección `defensa:` de la
configuración y **se pueden desactivar** (poner a 0), para medir el sistema sin
ellas y para no romper la interoperabilidad que pide el enunciado. Los umbrales
están calibrados con LFW (`scripts/evaluar.py`), no puestos a ojo.

| # | Ataque | Defensa | Estado |
|---|---|---|---|
| 1 | Envenenar la inscripción (añadir mi cara como muestra de la víctima) | `coherencia_muestra`: la muestra nueva debe parecerse a las anteriores | ✅ |
| R | Cualquiera inscribe, borra o lee la BD por la web | Dos roles: usuario (verificar/identificar) y admin con PIN | ✅ |
| 2 | Template fabricado por `_t` | Firmar los templates que genera el sistema; `_t` avisa si no viene firmado | ✅ |
| 3 | Hill-climbing con la puntuación | Redondear la puntuación y limitar intentos | ✅ |
| 4 | Gemelos / dobles en identificación | Margen mínimo entre el 1.º y el 2.º candidato | ⏳ |
| 5 | `_sd` con una imagen que no es una cara | Comprobar que hay cara aunque no se alinee | ⏳ |
| 6 | Leer la BD / rollback | Cifrado por defecto; versión firmada aparte | ⏳ |
| 7 | Foto de una foto (presentación) | Reto de vivacidad en la web; documentado como límite en CLI | ⏳ |

---

## Paso 1 · Envenenar la inscripción

**El ataque.** El atacante consigue añadir **su** cara como una muestra más de la
víctima. En la web es trivial: en la ficha de cualquier persona hay un botón
"Añadir foto". Si el atacante sube ahí su propia cara, a partir de ese momento la
base de datos tiene, bajo el nombre de la víctima, un template que es el del
atacante.

**Qué pasaba sin defensa.** Como la puntuación frente a una persona es el
**máximo** sobre sus muestras (`bd.agregacion: max`), basta una muestra
envenenada para que el atacante **verifique y se identifique como la víctima**
para siempre, aunque la víctima no haya hecho nada. Demostrado: tras el
envenenamiento, `verificar("Jack Straw", foto_del_atacante)` devuelve `aceptado:
true`.

**La defensa.** Al **añadir** una muestra a una persona que **ya existe**, el
sistema exige que el nuevo template se parezca a los que esa persona ya tiene,
al menos `defensa.coherencia_muestra` (0,20). Si no llega, lo rechaza con el
error `muestra_incoherente`. Está en un único sitio, `SistemaBiometrico._inscribir`,
así que cubre los tres métodos de inscripción (`inscribir`, `inscribir_sd`,
`inscribir_t`) y también la web.

No se aplica a la **primera** muestra de una persona nueva: no hay con qué
comparar, y decidir si alguien es quien dice al crearlo es responsabilidad de
quien inscribe (ver paso 7 y la inscripción supervisada), no de esta
comprobación.

**Por qué 0,20.** Medido en LFW (`scripts/evaluar.py`, 200 personas):

| Umbral | Rechaza muestras legítimas | Deja pasar caras ajenas |
|---|---|---|
| 0,15 | 0,8 % | 0,75 % |
| **0,20** | **1,1 %** | **0,095 %** |
| 0,25 | 1,1 % | 0,016 % |

0,20 bloquea el 99,9 % de las caras ajenas rechazando solo el 1 % de las fotos
legítimas. Es el mismo valor por debajo del cual la web ya consideraba que dos
caras "no se parecen".

**Límite conocido.** No para a quien envenena con una foto que **de verdad se
parece** a la víctima (un doble, un gemelo, un morph bien hecho: ver pasos 4 y
7). Sí para el caso fácil y habitual: colar una cara cualquiera.

**Cómo se comprueba.** `scripts/atacar.py --solo envenenar_muestra`, y el test
`test_defensas.py::test_coherencia_bloquea_envenenamiento`.

---

## Paso de roles · Quién puede ver y tocar la base de datos

**El ataque.** La web (sobre todo publicada con `--tunel`) daba a **cualquiera**
que tuviera la URL el control total: inscribir, borrar personas, renombrar, y
ver toda la base de datos con las fotos y los porcentajes de parecido. No hacía
falta saltarse nada: era el comportamiento normal. Un visitante podía
envenenar la BD (paso 1), borrarla entera o sacar la lista de quién está inscrito.

**Qué pasaba sin defensa.** No había ninguna distinción de permisos. La única
barrera era que el servidor solo escucha en localhost (y el dominio del túnel),
pero una vez dentro, todo estaba abierto.

**La defensa: dos roles.**

- **Usuario** (por defecto, sin contraseña): puede **verificar** e
  **identificar**. Al identificar ve el veredicto y el nombre ("Es X" o
  "Desconocido") y el porcentaje, pero **no la foto guardada en la BD ni la lista
  de personas más parecidas**. No puede inscribir, borrar ni abrir la pestaña
  "Base de datos", y `GET /api/estado` no le dice cuánta gente hay ni con qué
  umbrales. Así no puede recorrer la base de datos.
- **Administrador** (con PIN): todo lo anterior más inscribir, editar, borrar y
  ver la base de datos completa.

**Cómo funciona.**

- El PIN se fija con `--admin-pass`, la variable de entorno
  `BIOMETRIA_ADMIN_PASS`, o se genera uno aleatorio que se imprime al arrancar.
- Desde la web, la pastilla de arriba a la derecha abre un diálogo para meter el
  PIN. Al acertar, el servidor crea una **sesión** (token aleatorio en memoria)
  y la entrega en una cookie `HttpOnly; SameSite=Strict`. "Salir" revoca el token.
- El servidor comprueba el rol en **cada** endpoint, no solo en la interfaz: pedir
  `POST /api/inscribir`, `GET /api/personas` o `DELETE /api/personas` sin sesión
  devuelve **403 `requiere_admin`**. Ocultar los botones en el navegador no es la
  defensa; la defensa está en el servidor.
- La comparación del PIN es en tiempo constante (`hmac.compare_digest`).

**Qué NO resuelve.** No hay límite de intentos del PIN (pendiente, va con el
paso 3) ni cuentas por persona: es un único rol de administrador, suficiente
para la práctica. El PIN viaja en claro si se usa por HTTP sin el túnel (en
localhost no sale del equipo; por el túnel de Cloudflare va sobre HTTPS).

**Cómo se comprueba.** `test_web.py`:
`test_login_logout_y_pin_incorrecto`,
`test_usuario_no_puede_ver_ni_editar_la_bd`,
`test_usuario_identifica_pero_sin_foto_ni_candidatos`.

---

## Paso 2 · Template fabricado por `_t`

**El ataque.** El enunciado obliga a ofrecer `inscribir_t`, `verificar_t` e
`identificar_t`, que reciben directamente un **template** (un vector de números),
saltándose el detector y el extractor. Un atacante que lea la base de datos puede
copiar el template de la víctima y pasarlo por `verificar_t`; o, sin leerla,
fabricar un vector a medida. Está comprobado: `verificar_t("Jack Straw", <su
template>)` devuelve `aceptado: true` con similitud 1,000.

**Qué pasaba sin defensa.** El sistema validaba del template solo la forma
(dimensión, norma, valores finitos). Un vector que pase esas comprobaciones se
compara como cualquier otro, venga de donde venga. No había forma de distinguir
un template que generamos nosotros de uno inventado.

**La defensa: firma.** El sistema **firma con HMAC-SHA256** los templates que
genera. La clave sale de la misma clave maestra que protege la base de datos
(derivada aparte, uso `template-sig`), así que solo quien tiene la clave puede
producir una firma válida.

- `extraer --salida t.npy` escribe también `t.npy.sig` con la firma.
- `verificar_t`/`identificar_t`/`inscribir_t` aceptan una `--firma` (o leen el
  `.sig` de al lado) y **devuelven un campo `fiable`**:
  - `true`: el template lo generó este sistema (firma válida).
  - `false`: la firma no cuadra → el template es fabricado o fue manipulado.
  - `null`: no se aportó firma (no se puede saber).

**Importante: avisa, no bloquea.** Los `_t` **siguen aceptando** cualquier
template, como exige el enunciado y para no romper la interoperabilidad con el
grupo rival. La firma no impide la operación: la **etiqueta**, para que quien use
el sistema (o nuestra propia evaluación) pueda rechazar o marcar como sospechoso
un resultado con `fiable: false`. En la web, los templates vienen siempre de una
foto que el servidor acaba de procesar, así que son legítimos.

**Qué NO resuelve.** Si el atacante tiene la clave maestra, puede firmar lo que
quiera. La firma protege frente a quien lee la BD o fabrica vectores **sin** la
clave, no frente a quien ya la tiene (ese es el paso 6: proteger la clave).

**Cómo se comprueba.** `test_defensas.py::test_firma_distingue_template_propio_de_fabricado`
y `::test_la_firma_depende_de_la_clave`.

---

## Paso 3 · Hill-climbing con la puntuación

**El ataque.** El sistema **devuelve la puntuación** (hace falta para evaluarlo:
curvas FAR/FRR, y el enunciado lo pide implícitamente). Un atacante puede
aprovecharla: partir de un template cualquiera y, probándolo muchas veces por
`verificar_t`, quedarse cada vez con la variación que **sube** la puntuación,
hasta superar el umbral. Comprobado: partiendo de puro ruido se llega a aceptar
a "Jack Straw" en unas **20 000 consultas** (~1,5 s), sin saber nada de él.

**Qué pasaba sin defensa.** La puntuación se devolvía con todos sus decimales y
no había límite de intentos, así que el barrido era rápido y gratis.

**La defensa: dos medidas que se complementan.**

1. **Redondear** la puntuación que sale por `_t` a `decimales_puntuacion_t` (2).
   Con menos resolución, muchas variaciones dan el mismo número y el atacante
   pierde el "gradiente" que lo guía. La **decisión** (aceptar o no) se toma con
   la puntuación exacta; solo se redondea lo que se enseña.
2. **Límite de intentos**: como mucho `max_intentos_t` (50) llamadas a
   `verificar_t`/`identificar_t` por id en `ventana_intentos_s` (60 s). Pasado el
   límite, `demasiados_intentos` (HTTP 429 en la web).

Con las dos juntas, el ataque anterior se queda en **50 intentos → puntuación
máxima 0,15** (umbral 0,35): a ese ritmo necesitaría ~7 horas de barrido
continuo, que es detectable y se puede cortar.

**Qué no cuenta para el límite.** `verificar`/`identificar` **con foto** no gastan
cupo: ahí el atacante tendría que generar una cara real por intento, no hay
hill-climbing por template. La web, que siempre procesa una foto antes, tampoco.

**Qué NO resuelve.** El límite es por proceso y en memoria (se reinicia al
arrancar), y no distingue IPs en la web (pendiente, iría con un rate-limit de
red). Redondear a 2 decimales deja algo de señal; es el **límite de intentos** lo
que hace inviable el barrido.

**Cómo se comprueba.** `test_defensas.py::test_la_puntuacion_de_t_se_redondea`,
`::test_limite_de_intentos_frena_el_barrido`,
`::test_las_operaciones_con_foto_no_gastan_el_cupo`.
