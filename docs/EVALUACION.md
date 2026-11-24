# Evaluación del sistema JumeXBiometrics

Resultados medidos del sistema propio (fase 2 del enunciado): rendimiento del
reconocimiento y comportamiento ante los ataques que pide el enunciado (el atacante
se salta partes del sistema, lee la base de datos o escribe en ella). Pensado para
copiarlo a la memoria.

- **Fechas:** 23 y 24/11/2026, rama `FINAL`.
- **Entorno:** el contenedor Docker del proyecto (Python 3.11.18, numpy 2.4.6, OpenCV 4.14.0) en un MacBook Pro M5.
- **Ficheros con los resultados en bruto:** `resultados/revision-2026-11/` (fuera de git).
- **Cómo repetirlo:** al final del documento.

---

## 1. Qué se evalúa

| | |
|---|---|
| **Pipeline** | YuNet (detección y 5 puntos) → control de calidad (cara ≥ 60 px, nitidez ≥ 40) → alineado a 112×112 → ArcFace R50 (`w600k_r50`, template de 512 valores, con la cara volteada sumada) → similitud `max(coseno, 0)` |
| **Decisión** | Verificación: aceptar si la similitud ≥ **0,35**. Identificación: el mejor candidato si ≥ **0,40** y saca al 2.º al menos **0,10** (si no, "ambiguo" → desconocido) |
| **Base de datos** | CSV con una fila por muestra: HMAC-SHA256 por fila, template cifrado con AES-256-GCM y sello anti-rollback. 50 personas: las 49 fotos del docente y Lionel Messi (elegido por el equipo) |
| **Datos de evaluación** | (a) **LFW** (*Labeled Faces in the Wild*), para medir errores con muchas personas y varias fotos de cada una. (b) **El Dataset de la práctica** (una foto por persona), para el comportamiento con la galería real y los ataques |

## 2. Verificación (1:1) e identificación (1:N) en LFW

`scripts/evaluar.py --personas 150 --fotos 4 --desconocidos 200 --semilla 11 --tabla`:
149 personas, 423 fotos útiles (12 descartadas por el detector o la calidad), **452 pares
genuinos** y **88 801 pares impostores**.

| | Media | p1 | Mediana | p99 | Máximo |
|---|---|---|---|---|---|
| Genuinos (misma persona) | 0,679 | 0,416 | 0,687 | 0,878 | 0,918 |
| Impostores (personas distintas) | 0,026 | 0,000 | 0,004 | 0,148 | 0,303 |

**EER: 0,22 %**, con umbral 0,187.

| Umbral | FAR | FRR |
|---|---|---|
| 0,15 | 0,926 % | 0,00 % |
| 0,20 | 0,117 % | 0,22 % |
| 0,25 | 0,024 % | 0,22 % |
| 0,30 | 0,001 % | 0,22 % |
| **0,35 (verificación)** | **0,000 %** | **0,22 %** |
| **0,40 (identificación)** | **0,000 %** | **0,66 %** |
| 0,50 | 0,000 % | 4,20 % |
| 0,60 | 0,000 % | 21,0 % |

**Identificación** contra 149 personas (274 consultas): **rank-1 99,64 %**; identificados
correctamente (rank-1 y supera 0,40) 99,27 %; **desconocidos confundidos con alguien:
0 de 191**. El p99 del 2.º candidato de un inscrito es 0,268 y el del mejor candidato
de un desconocido 0,260: ambos muy por debajo de 0,40.

Una evaluación anterior, más grande (392 personas, 1173 genuinos y 615 432 impostores),
dio EER 0,68 % (umbral 0,158), rank-1 99,2 % y un 0,17 % de desconocidos aceptados
(`README.md`, "Cuánto acierta"). Con los umbrales de la configuración: FAR 0,0005 % y
FRR 0,85 % en verificación, y FRR 1,02 % en identificación.

**Comparación de extractores** (misma evaluación, 200 personas):

| | ArcFace | SFace | LBP |
|---|---|---|---|
| EER | **0,82 %** | 1,60 % | ≈ 37 % |
| Rank-1 | **99,2 %** | 98,4 % | 11 % |

## 3. La galería real (Dataset de la práctica)

**Detección y calidad.** Las 50 fotos se detectan e inscriben (unos 50 ms por foto):
- La cara más pequeña es la de Leticia Sabater (82 px, mínimo 60) y la menos nítida la de Temuera Morrison (219, mínimo 40).
- Dos fotos tienen varias caras: `adam_pearson_2024.jpg` (3) y `julie_walters_2007.jpg` (2). Se usa la mayor (`varias_caras: mayor`).
- Dos ficheros no son lo que dice su extensión: la foto de Aitana Etxebarria es AVIF y la de Falete WebP. OpenCV los abre igualmente.

**Separación entre las personas inscritas** (1176 pares entre las 49 del docente):

| Media | Máximo | Pares ≥ 0,15 | ≥ 0,20 | ≥ 0,30 | ≥ 0,35 |
|---|---|---|---|---|---|
| 0,026 | **0,25** | 6 | 2 | **0** | **0** |

- **Parejas más parecidas:** Salma Hayek–Tini (0,25), Keanu Reeves–Temuera Morrison (0,20) y Arianna Grande–Taylor Swift (0,19).
- **Messi:** antes de inscribirlo, identificar con su foto daba desconocido (mejor candidato, 0,13).
- **Lectura:** ningún par de inscritos se acerca al umbral, así que la galería no tiene "dobles" peligrosos.

**Gemelos.** El Dataset incluye a Aitana Etxebarria, James Phelps y Mary-Kate Olsen, cuyos gemelos no están inscritos. Una foto del gemelo probablemente se verificaría como su hermano: riesgo aceptado en `THREAT_MODEL.md`.

**Robustez** (la foto de inscripción de cada persona, transformada, verificada contra esa persona; umbral 0,35):

| Variante | Aceptadas | Similitud mínima | Media |
|---|---|---|---|
| JPEG de calidad 30 | 49/49 | 0,94 | 0,97 |
| Mitad de tamaño | 48/49 (1 `cara_pequena`) | 0,90 | 0,98 |
| Espejo | 49/49 | 0,78 | 0,97 |
| Girada 12° | 49/49 | 0,85 | 0,97 |
| Más oscura (gamma 2) | 49/49 | 0,87 | 0,97 |
| Más clara (gamma 0,5) | 49/49 | 0,77 | 0,97 |
| Escala de grises | 49/49 | 0,84 | 0,95 |
| Desenfoque leve | 49/49 | 0,93 | 0,98 |
| Ojos tapados (franja negra) | 49/49 | 0,48 | 0,70 |
| Mascarilla (mitad inferior tapada) | 48/49 (1 `sin_cara`) | 0,50 | 0,72 |

**Métodos sin detector (`_sd`).** Con la propia cara recortada y sin alinear (no hay puntos clave):
- **Recorte con margen:** media 0,74 y mínimo 0,33; aceptadas 48/49.
- **Recorte justo a la caja:** mínimo 0,35; aceptadas 49/49.
- **`identificar_sd`:** acierta 47 de 49.
- **Lectura:** funciona, pero con mucho menos margen que el pipeline completo (alinear importa). La comprobación de que hay una cara rechaza 21/21 recortes de fondo y el ruido; en LFW acepta el 100 % de los recortes de cara legítimos y rechaza el 99,1 % de los trozos de fondo (`scripts/medir_defensas.py`).

**Tiempos.** Unos 45 ms por verificación o identificación con foto, y unos 50 ms por inscripción.

## 4. Seguridad: los supuestos del enunciado

Cada ataque se lanzó contra el sistema real, con una BD temporal
(`scripts/pruebas_revision.py`), y la mayoría también está cubierta por los tests (`tests/test_defensas.py`, `tests/test_web.py`).
"Antes" se refiere al estado del 23/11 y "ahora" al de después de las correcciones del 24/11.

### 4.1 El atacante se salta partes del sistema

| Ataque | Resultado |
|---|---|
| `verificar_t` con un template **sin firma**, **copiado de la BD** o **retocado** | Rechazado (`template_no_firmado`) en los tres casos |
| `verificar_t` con un template de dimensión incorrecta o con NaN | Rechazado (`template_invalido`) |
| **Hill-climbing** (ajustar un template desde ruido hasta superar 0,35) | Sin la firma bastan **216 consultas** (393 con la puntuación redondeada a 2 decimales), en menos de 1 s. **Con la firma activa, el primer retoque ya se rechaza** sin dar puntuación |
| Límite de intentos de `_t` | Salta en el intento 51 (50 por minuto). Se reinicia en cada proceso nuevo: solo protege a la web |
| `_sd` con ruido o con un trozo de fondo | Rechazado (`sin_cara`) |
| Web: "cara" marcada a mano sobre ruido | Antes llegaba al extractor (`manual_sin_puntos`, 0,15). **Ahora `sin_cara`** |

### 4.2 El atacante lee la base de datos

- Los 50 templates están cifrados (`gcm:` en todas las filas): leer el CSV no da los vectores.
- La clave está fuera de `data/` y del repositorio (`~/.jumex/`).
- Aunque copie un template descifrado, `_t` no lo acepta sin firma (ver 4.1).

### 4.3 El atacante escribe en la base de datos

| Manipulación del CSV | Resultado |
|---|---|
| Cambiar un carácter de un template | `integridad_bd` |
| Intercambiar dos filas | `integridad_bd` |
| Borrar una fila | `integridad_bd` |
| Duplicar una fila | `integridad_bd` |
| Mover el template de una persona a otra | `integridad_bd` |
| Renombrar una fila | `integridad_bd` |
| Poner una fórmula de Excel como id | `integridad_bd` |
| Volver a una copia antigua válida (rollback) | `integridad_bd` |
| Borrar la BD | `integridad_bd` ("ha desaparecido") |
| Abrirla con otra clave | `integridad_bd` |
| **Borrar el sello y volver a una copia antigua** | Antes **se aceptaba** (48 personas en vez de 49). **Ahora `integridad_bd`** (`sello_estricto`) |

### 4.4 Otros ataques probados

| Ataque | Antes | Ahora |
|---|---|---|
| Inscribir la cara del atacante como muestra de la víctima | Rechazado (`muestra_incoherente`) | Rechazado |
| **Deriva con morphs:** 8 mezclas progresivas víctima → atacante como muestras de la víctima | **8 de 8 parejas con éxito**: el atacante pasaba de 0,00–0,06 a 0,92–0,98 | **0 de 8**: la cadena se corta entre el 36 % y el 60 % de mezcla y el atacante sigue en 0,00–0,06 (coherencia contra la primera muestra + ancla) |
| **La misma cara con otro nombre** | La identificación de la víctima salía `ambiguo` → desconocido | `cara_ya_inscrita` al crear la persona nueva |
| **La foto exacta del Dataset** (está en el repositorio) | Verificar da **1,00** para las 50 | Igual: **riesgo aceptado** |
| Nombres maliciosos (fórmula, salto de línea, ruta, emoji, más de 100 caracteres) | Rechazados (`id_invalido`) | Igual |
| El nombre de un inscrito en minúsculas o sin tildes (`lionel messi`) | Rechazado (`id_no_inscrito`): un falso rechazo que no era del reconocimiento | Encuentra a la persona y devuelve su id real. Las erratas de letra siguen sin encontrarse |
| Imágenes malas (basura, vacía, más de 25 MP, cara de 40 px, muy borrosa, negra) | Rechazadas con su error | Igual |
| Web sin PIN: admin, inscribir, CSRF, path traversal, 11 MB, 61 fotos por minuto | 403, 403, 403, 404, 413, 429 | Igual |
| Web sin PIN: enumerar inscritos con verificar | No se distingue (puntuación de impostor estable, mismo tiempo) | Igual |
| Web sin PIN: lo que devuelve una respuesta | Los 512 valores del template y el número de inscritos | Solo la dimensión del template |

### 4.5 Qué no se para (riesgos aceptados)

Detalle en `THREAT_MODEL.md`:
- presentación (foto, pantalla, máscara) y deepfakes o morphs presentados directamente;
- gemelos no inscritos;
- ejemplos adversarios contra ArcFace (el modelo es público);
- la foto exacta del repositorio;
- el **rival como operador** del sistema (con la clave en su máquina, el cifrado, la firma y el sello no le frenan);
- cambios de aspecto legítimos, que causan falsos rechazos.

## 5. Limitaciones de esta evaluación

- **Umbrales y medida con los mismos datos de LFW:** las cifras son algo optimistas. Falta separar personas de calibración y de test.
- **Pocos errores en la muestra:** un FAR del 0,000 % con 88 801 impostores solo dice que es menor que ~0,003 %. El FRR del 0,22 % es 1 de 452 pares.
- **Una sola foto por persona en el Dataset:** no se puede medir el FRR con la galería real. Hacen falta 2 o 3 fotos más por persona.
- **Faltan las curvas DET, ROC y CMC** (los datos ya los da `evaluar.py`).
- **Falta la evaluación del sistema rival** (fase 3).

## 6. Cómo repetirlo

```bash
docker compose --profile pruebas run --rm pruebas
docker compose --profile pruebas run --rm --entrypoint python pruebas scripts/evaluar.py --personas 150 --fotos 4 --desconocidos 200 --semilla 11 --tabla
docker compose --profile pruebas run --rm --entrypoint python pruebas scripts/medir_defensas.py
docker compose up -d
docker compose exec -T web python scripts/pruebas_revision.py > resultados_revision.json
```

El primero lanza los tests; el segundo, la evaluación con LFW (descarga unos 180 MB la
primera vez); el tercero, el coste de las defensas en LFW; los dos últimos, la batería de
ataques. `scripts/pruebas_revision.py` trabaja con una BD, una clave y un sello temporales en
`/tmp`: no toca `data/lens` ni `~/.jumex`.
