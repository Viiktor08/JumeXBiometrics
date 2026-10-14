/* JumeX Lens · lógica de la interfaz.
   Sin dependencias. La API devuelve cada paso del pipeline (detecciones,
   cara alineada, template, tiempos) y aquí se anima en ese orden. */
"use strict";

const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => [...raiz.querySelectorAll(sel)];
const esperar = (ms) => new Promise((r) => setTimeout(r, ms));
const pct = (x) => `${Math.round(x * 100)}%`;
const reduceMovimiento = matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Crea un elemento: el("div", {class: "x", text: "hola"}, hijo1, hijo2) */
function el(tag, attrs = {}, ...hijos) {
  const nodo = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") nodo.className = v;
    else if (k === "text") nodo.textContent = v;
    else if (k.startsWith("on")) nodo.addEventListener(k.slice(2), v);
    else nodo.setAttribute(k, v === true ? "" : v);
  }
  for (const h of hijos.flat()) if (h != null) nodo.append(h);
  return nodo;
}

function icono(nombre) {
  const trazos = {
    si: '<path d="M20 6 9 17l-5-5"/>',
    no: '<path d="M18 6 6 18M6 6l12 12"/>',
    duda: '<path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/><circle cx="12" cy="12" r="10"/>',
    aviso: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    persona: '<circle cx="10" cy="8" r="4"/><path d="M3 21a7 7 0 0 1 14 0M19 8v6M16 11h6"/>',
    lupa: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    igual: '<path d="M5 9h14M5 15h14"/>',
    distinto: '<path d="M5 9h14M5 15h14M16 5 8 19"/>',
    escudo: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    estrella: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
    mas: '<path d="M12 5v14M5 12h14"/>',
    papelera: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
    mano: '<path d="M18 11V6a2 2 0 0 0-4 0M14 10V4a2 2 0 0 0-4 0v2M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
    camara: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3.5"/>',
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = trazos[nombre] || "";
  return svg;
}

function iniciales(nombre) {
  return nombre.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
}

/* ------------------------------------------------------------------ */
/* Estado                                                              */
/* ------------------------------------------------------------------ */
const estado = {
  modo: "verificar",
  admin: false,          // rol: false = usuario (por defecto), true = administrador
  personas: [],          // [{id, muestras, foto}] (solo admin)
  sistema: null,         // /api/estado
  archivo: null,         // File pendiente o analizado
  bitmap: null,          // ImageBitmap de la foto
  ocupado: false,
  ultimaRespuesta: null,
  ultimaOperacion: null,
  inscritoReciente: null,
  caja: null,            // cara elegida [x, y, ancho, alto] en píxeles de la foto
  cajaOrigen: null,      // "manual" (la marcó el usuario) o "confirmada" (detectada y confirmada al inscribir)
  seleccion: null,       // modo "marcar a mano": {inicio, actual, rect}
};

const MODOS = {
  verificar: {
    antetitulo: "Verificación 1:1",
    titulo: "¿Es quien dice ser?",
    descripcion: "Elige una persona de la base de datos y suelta una foto. El sistema la compara solo con esa persona y responde sí o no.",
    zona: "Suelta tu foto para verificar",
    boton: "Verificar",
    pideNombre: true,
    icono: "si",
    comparacion: ["Comparación 1:1", "Con la persona indicada"],
  },
  identificar: {
    antetitulo: "Identificación 1:N",
    titulo: "¿Quién es?",
    descripcion: "Suelta una foto y el sistema la compara con todas las personas de la base de datos para decirte quién es… o que no la conoce.",
    zona: "Suelta tu foto para identificar",
    boton: "Identificar",
    pideNombre: false,
    icono: "lupa",
    comparacion: ["Comparación 1:N", "Con toda la base de datos"],
  },
  inscribir: {
    antetitulo: "Inscripción",
    titulo: "Añadir a la base de datos",
    descripcion: "Escribe el nombre y suelta una foto. Se guarda su template (no la foto). Si la persona ya existe, se añade como otra muestra.",
    zona: "Suelta la foto para inscribir",
    boton: "Inscribir",
    pideNombre: true,
    icono: "persona",
    comparacion: ["Guardado en la BD", "Template firmado con HMAC"],
  },
};

const PASOS = ["carga", "deteccion", "alineado", "extraccion", "comparacion"];

// Paso en el que se produce cada error y texto amable para el usuario
const ERRORES = {
  imagen_invalida: ["carga", "No puedo abrir esa imagen", "Usa una foto JPG o PNG. Las fotos HEIC del iPhone no están soportadas: expórtala como JPG."],
  imagen_demasiado_grande: ["carga", "La imagen es demasiado grande", "Prueba con una foto de menos de 10 MB o reduce su resolución."],
  sin_cara: ["deteccion", "No encuentro ninguna cara", "Prueba con una foto de frente, bien iluminada y en la que la cara se vea entera."],
  varias_caras: ["deteccion", "Hay varias caras en la foto", "Usa una foto en la que salga una sola persona."],
  cara_pequena: ["alineado", "La cara sale demasiado pequeña", "Usa una foto más cercana o recórtala alrededor de la cara."],
  imagen_borrosa: ["alineado", "La cara está borrosa", "La foto está movida, desenfocada o muy comprimida. Prueba con otra más nítida."],
  template_invalido: ["extraccion", "No se pudo calcular el template", null],
  id_invalido: ["comparacion", "Nombre no válido", null],
  id_no_inscrito: ["comparacion", "Esa persona no está en la base de datos", "Revisa el nombre (usa las sugerencias) o inscríbela antes en la pestaña Inscribir."],
  limite_muestras: ["comparacion", "Esa persona ya tiene el máximo de fotos", null],
  muestra_incoherente: ["comparacion", "Esta cara no se parece a esa persona", "La foto no coincide con las que ya tiene guardadas. Si de verdad es esa persona, usa una foto más parecida; si es otra, inscríbela con su propio nombre."],
  requiere_admin: ["comparacion", "Necesitas ser administrador", "Esta acción (inscribir, borrar o ver la base de datos) es solo del administrador. Entra con el PIN desde la pastilla de arriba a la derecha."],
  demasiados_intentos: ["comparacion", "Demasiados intentos seguidos", "Se han hecho muchas comparaciones en poco tiempo. Es una protección contra el ajuste automático de plantillas. Espera un momento y vuelve a intentarlo."],
  integridad_bd: ["comparacion", "¡La base de datos ha sido manipulada!", "La firma HMAC no cuadra: alguien ha modificado bd.csv fuera del sistema. Por seguridad no se usa."],
  bd_corrupta: ["comparacion", "La base de datos está dañada", null],
  configuracion_invalida: ["carga", "Error de configuración del servidor", null],
};

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */
async function api(ruta, opciones = {}) {
  const respuesta = await fetch(ruta, opciones);
  let datos;
  try { datos = await respuesta.json(); } catch { datos = null; }
  if (!datos) throw new Error(`El servidor respondió ${respuesta.status}`);
  return datos;
}

// cargarEstado fija estado.admin; cargarPersonas lo mira, así que van EN ORDEN
async function recargar() {
  await cargarEstado();
  await cargarPersonas();
}

async function cargarEstado() {
  const pastilla = $("#estado-sistema");
  try {
    const s = await api("/api/estado");
    estado.sistema = s;
    estado.admin = !!s.admin;
    aplicarRol();
    const nombreExtractor = { arcface: "ArcFace", sface: "SFace", lbp: "LBP" }[s.extractor] || s.extractor;
    if (estado.admin) {
      // El admin ve el detalle del sistema: integridad, nº de personas y umbrales
      pastilla.classList.toggle("ok", s.integridad === "ok");
      pastilla.classList.toggle("mal", s.integridad !== "ok");
      $("#estado-texto").textContent = s.integridad === "ok"
        ? `${nombreExtractor} · ${s.personas} personas · BD íntegra`
        : "¡Base de datos manipulada!";
      $("#pestana-bd-detalle").textContent = `${s.personas} personas inscritas`;
      $("#pie-extractor").textContent =
        `Extractor ${nombreExtractor} (${s.dimension} valores) · umbral verificación ${pct(s.umbrales.verificacion)} · identificación ${pct(s.umbrales.identificacion)} · parecido ${pct(s.umbrales.parecido)}`;
    } else {
      // El usuario solo ve que el sistema está en marcha, sin cifras de la BD
      pastilla.classList.add("ok");
      pastilla.classList.remove("mal");
      $("#estado-texto").textContent = `${nombreExtractor} · listo`;
      $("#pie-extractor").textContent = `Extractor ${nombreExtractor} (${s.dimension} valores)`;
    }
  } catch {
    pastilla.classList.add("mal");
    $("#estado-texto").textContent = "Sin conexión con el servidor";
  }
}

/** Ajusta la interfaz al rol: pastilla, pestañas de admin y autocompletado de nombres. */
function aplicarRol() {
  const rol = $("#rol");
  rol.classList.toggle("es-admin", estado.admin);
  $("#rol-texto").textContent = estado.admin ? "Administrador" : "Usuario";
  rol.setAttribute("title", estado.admin ? "Sesión de administrador. Pulsa para salir." : "Modo usuario. Pulsa para entrar como administrador.");
  $$(".pestana--admin").forEach((b) => { b.hidden = !estado.admin; });
  // Si el usuario estaba en una pestaña de admin y ha dejado de serlo, vuelve a verificar
  if (!estado.admin && (estado.modo === "inscribir" || estado.modo === "bd")) cambiarModo("verificar");
}

async function cargarPersonas() {
  // La lista de personas es solo del admin (alimenta las sugerencias de verificar y la pestaña BD)
  if (!estado.admin) {
    estado.personas = [];
    $("#lista-nombres").replaceChildren();
    return;
  }
  try {
    const datos = await api("/api/personas");
    if (datos.error) throw new Error(datos.error.mensaje);
    estado.personas = datos.personas;
  } catch {
    estado.personas = [];
  }
  const lista = $("#lista-nombres");
  lista.replaceChildren(...estado.personas.map((p) => el("option", { value: p.id })));
  actualizarAyudaNombre();
  pintarBD();
}

/* ------------------------------------------------------------------ */
/* Login de administrador                                              */
/* ------------------------------------------------------------------ */
function abrirLogin() {
  if (estado.admin) { cerrarSesion(); return; }
  const d = $("#login");
  $("#login-pin").value = "";
  $("#login-error").hidden = true;
  d.showModal();
  $("#login-pin").focus();
}

async function entrarAdmin(e) {
  e.preventDefault();
  const pin = $("#login-pin").value;
  if (!pin) return;
  try {
    const r = await api("/api/login", { method: "POST", headers: { "X-JumeX-Lens": "1" }, body: pin });
    if (!r.ok) throw new Error((r.error && r.error.mensaje) || "PIN incorrecto");
    $("#login").close();
    avisar("Has entrado como administrador");
    await recargar();
  } catch (err) {
    const e2 = $("#login-error");
    e2.textContent = err.message;
    e2.hidden = false;
    $("#login-pin").select();
  }
}

async function cerrarSesion() {
  try { await api("/api/logout", { method: "POST", headers: { "X-JumeX-Lens": "1" } }); } catch { /* da igual */ }
  avisar("Has salido del modo administrador");
  await recargar();
  cambiarModo("verificar");
}

const buscarPersona = (nombre) => {
  const n = nombre.trim().normalize("NFC").toLocaleLowerCase("es");
  return estado.personas.find((p) => p.id.toLocaleLowerCase("es") === n);
};

/* ------------------------------------------------------------------ */
/* Modos y pestañas                                                    */
/* ------------------------------------------------------------------ */
function cambiarModo(modo, { conservarFoto = false } = {}) {
  estado.modo = modo;
  $$(".pestana").forEach((b) => {
    const activa = b.dataset.modo === modo;
    b.classList.toggle("activa", activa);
    b.setAttribute("aria-selected", activa);
  });
  const esBD = modo === "bd";
  $("#vista-operacion").hidden = esBD;
  $("#vista-bd").hidden = !esBD;
  if (esBD) { pintarBD(); return; }

  const m = MODOS[modo];
  $("#modo-antetitulo").textContent = m.antetitulo;
  $("#modo-titulo").textContent = m.titulo;
  $("#modo-descripcion").textContent = m.descripcion;
  $("#modo-icono").replaceChildren(icono(m.icono));
  $("#zona-titulo").textContent = m.zona;
  $("#campo-nombre").hidden = !m.pideNombre;
  $("#nombre").placeholder = modo === "inscribir" ? "Nombre y apellidos (p. ej. Ana López)" : "Empieza a escribir… (p. ej. Jack Straw)";
  $("#nombre").setAttribute("list", modo === "verificar" ? "lista-nombres" : "");
  $("#paso-comparacion-titulo").textContent = m.comparacion[0];
  $("#paso-comparacion-detalle").textContent = m.comparacion[1];
  actualizarAyudaNombre();

  if (conservarFoto && estado.archivo) {
    reiniciarPipeline();
    pintarResultadoVacio();
    salirSeleccion(false);
    dibujarFotoQuieta();
    mostrarBotones({ ejecutar: true, otra: true, manual: !!estado.bitmap && !!estado.ultimaRespuesta?.imagen });
  } else {
    reiniciarTodo();
  }
}

function actualizarAyudaNombre() {
  const ayuda = $("#nombre-ayuda");
  const avatar = $("#nombre-avatar");
  const nombre = $("#nombre").value;
  const persona = nombre.trim() ? buscarPersona(nombre) : null;
  ayuda.className = "campo__ayuda";
  avatar.hidden = true;
  if (estado.modo === "verificar") {
    if (!estado.admin) {
      // El usuario no ve la lista de la BD: escribe el nombre y comprueba
      ayuda.textContent = "Escribe el nombre de la persona y sube su foto para comprobar si coincide.";
    } else if (!nombre.trim()) ayuda.textContent = `${estado.personas.length} personas disponibles. Escribe para ver sugerencias.`;
    else if (persona) {
      ayuda.textContent = `✓ ${persona.id} está en la base de datos (${persona.muestras} muestra${persona.muestras > 1 ? "s" : ""})`;
      ayuda.classList.add("ok");
      if (persona.foto) { avatar.src = persona.foto; avatar.hidden = false; }
    } else { ayuda.textContent = "No hay nadie con ese nombre en la base de datos."; ayuda.classList.add("aviso"); }
  } else if (estado.modo === "inscribir") {
    if (persona) {
      ayuda.textContent = `${persona.id} ya existe: la foto se añadirá como otra muestra (${persona.muestras} → ${persona.muestras + 1}).`;
      ayuda.classList.add("aviso");
      if (persona.foto) { avatar.src = persona.foto; avatar.hidden = false; }
    } else ayuda.textContent = "Letras, números, espacios y . - _ ' (máx. 100 caracteres).";
  }
}

/* ------------------------------------------------------------------ */
/* Zona de soltar                                                      */
/* ------------------------------------------------------------------ */
function prepararZona() {
  const zona = $("#zona");
  const selector = $("#selector");
  const abrir = () => { if (!estado.ocupado && !estado.archivo) selector.click(); };

  zona.addEventListener("click", abrir);
  zona.addEventListener("keydown", (e) => {
    if (e.target !== zona) return; // p. ej. Enter sobre el botón de la cámara
    if ((e.key === "Enter" || e.key === " ") && !estado.archivo) { e.preventDefault(); abrir(); }
  });
  $("#boton-camara").addEventListener("click", (e) => {
    e.stopPropagation(); // que no abra también el selector de ficheros
    if (!estado.ocupado) abrirCamara(recibirArchivo);
  });
  selector.addEventListener("change", () => {
    if (selector.files[0]) recibirArchivo(selector.files[0]);
    selector.value = "";
  });
  ["dragenter", "dragover"].forEach((t) => zona.addEventListener(t, (e) => {
    e.preventDefault();
    if (!estado.ocupado) zona.classList.add("encima");
  }));
  ["dragleave", "drop"].forEach((t) => zona.addEventListener(t, () => zona.classList.remove("encima")));
  zona.addEventListener("drop", (e) => {
    e.preventDefault();
    const archivo = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/")) || e.dataTransfer?.files?.[0];
    if (archivo) recibirArchivo(archivo);
  });
  // Soltar fuera de la zona no debe abrir la imagen en el navegador
  ["dragover", "drop"].forEach((t) => window.addEventListener(t, (e) => e.preventDefault()));
  window.addEventListener("paste", (e) => {
    if (estado.modo === "bd") return;
    const archivo = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
    if (archivo) { e.preventDefault(); recibirArchivo(archivo); }
  });
}

async function recibirArchivo(archivo) {
  if (estado.ocupado) return;
  if (archivo.size > 10 * 1024 * 1024) {
    avisar("La foto supera 10 MB. Prueba con una más pequeña.");
    return;
  }
  estado.archivo = archivo;
  estado.caja = null;
  estado.cajaOrigen = null;
  salirSeleccion(false);
  try {
    estado.bitmap = await createImageBitmap(archivo);
  } catch {
    estado.bitmap = null; // p. ej. HEIC: el navegador no la abre, pero dejamos que el servidor lo diga
  }
  mostrarEscenario();
  reiniciarPipeline();
  pintarResultadoVacio();

  const m = MODOS[estado.modo];
  if (m.pideNombre && !$("#nombre").value.trim()) {
    dibujarFotoQuieta();
    $("#escenario-estado").textContent = "Escribe el nombre para continuar";
    mostrarBotones({ ejecutar: true, otra: true });
    pedirNombre();
    return;
  }
  ejecutar();
}

function pedirNombre() {
  const campo = $("#campo-nombre");
  campo.classList.remove("temblor");
  void campo.offsetWidth;
  campo.classList.add("temblor");
  campo.addEventListener("animationend", () => campo.classList.remove("temblor"), { once: true });
  $("#nombre").focus();
}

function mostrarEscenario() {
  $("#zona").classList.add("con-foto");
  $("#zona-vacia").hidden = true;
  $("#escenario").hidden = false;
  ajustarLienzo();
}

function mostrarBotones({ ejecutar = false, otra = false, manual = false, usar = false, cancelar = false }) {
  const boton = $("#boton-ejecutar");
  boton.hidden = !ejecutar;
  boton.textContent = `${MODOS[estado.modo]?.boton || "Analizar"} esta foto`;
  $("#boton-otra").hidden = !otra;
  $("#boton-otra-camara").hidden = !otra;
  $("#boton-manual").hidden = !manual;
  $("#boton-usar").hidden = !usar;
  $("#boton-cancelar").hidden = !cancelar;
}

/** ¿Tiene sentido ofrecer "marcar la cara a mano" tras esta respuesta? */
function puedeMarcar(respuesta) {
  if (!estado.bitmap || !respuesta?.imagen) return false;
  const codigo = respuesta.error?.error;
  return !["imagen_invalida", "imagen_demasiado_grande", "red", "integridad_bd", "bd_corrupta"].includes(codigo);
}

function reiniciarTodo() {
  estado.archivo = null;
  estado.bitmap = null;
  estado.ultimaRespuesta = null;
  estado.caja = null;
  estado.cajaOrigen = null;
  salirSeleccion(false);
  detenerAnimacion();
  $("#zona").classList.remove("con-foto");
  $("#zona-vacia").hidden = false;
  $("#escenario").hidden = true;
  $("#escenario-estado").textContent = "";
  mostrarBotones({});
  reiniciarPipeline();
  pintarResultadoVacio();
}

/* ------------------------------------------------------------------ */
/* Lienzo: foto, escaneo y detecciones                                 */
/* ------------------------------------------------------------------ */
const lienzo = { canvas: null, ctx: null, ancho: 0, alto: 0, marco: null, raf: 0 };

function ajustarLienzo() {
  const canvas = $("#lienzo");
  const caja = $("#zona").getBoundingClientRect();
  const ancho = Math.max(caja.width - 4, 200);
  const proporcion = estado.bitmap ? estado.bitmap.height / estado.bitmap.width : 0.75;
  const alto = Math.round(Math.min(Math.max(ancho * proporcion, 280), 440));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(ancho * dpr);
  canvas.height = Math.round(alto * dpr);
  canvas.style.height = `${alto}px`;
  lienzo.canvas = canvas;
  lienzo.ctx = canvas.getContext("2d");
  lienzo.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  lienzo.ancho = ancho;
  lienzo.alto = alto;
  // Rectángulo donde cabe la foto entera (como object-fit: contain)
  if (estado.bitmap) {
    const escala = Math.min(ancho / estado.bitmap.width, alto / estado.bitmap.height);
    const w = estado.bitmap.width * escala, h = estado.bitmap.height * escala;
    lienzo.marco = { x: (ancho - w) / 2, y: (alto - h) / 2, w, h };
  } else {
    lienzo.marco = { x: 0, y: 0, w: ancho, h: alto };
  }
}

function detenerAnimacion() { cancelAnimationFrame(lienzo.raf); lienzo.raf = 0; }

function pintarFoto(oscurecer = 0) {
  const { ctx, ancho, alto, marco } = lienzo;
  ctx.clearRect(0, 0, ancho, alto);
  ctx.fillStyle = "#06122e";
  ctx.fillRect(0, 0, ancho, alto);
  if (estado.bitmap) ctx.drawImage(estado.bitmap, marco.x, marco.y, marco.w, marco.h);
  else {
    ctx.fillStyle = "#7dd3fc";
    ctx.font = "600 15px 'Plus Jakarta Sans', sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Vista previa no disponible para este formato", ancho / 2, alto / 2);
  }
  if (oscurecer > 0) {
    ctx.fillStyle = `rgba(6, 18, 46, ${oscurecer})`;
    ctx.fillRect(0, 0, ancho, alto);
  }
}

function dibujarFotoQuieta() {
  detenerAnimacion();
  ajustarLienzo();
  pintarFoto(0.15);
}

/** Escaneo mientras el servidor procesa: línea de luz que barre la foto. */
function animarEscaneo() {
  detenerAnimacion();
  const inicio = performance.now();
  const periodo = 1500;
  const cuadro = (ahora) => {
    const { ctx, ancho, marco } = lienzo;
    pintarFoto(0.38);
    const t = ((ahora - inicio) % periodo) / periodo;
    const fase = t < 0.5 ? t * 2 : 2 - t * 2; // va y vuelve
    const y = marco.y + fase * marco.h;

    // Rejilla de puntos que se ilumina cerca de la línea
    ctx.save();
    for (let gy = marco.y + 12; gy < marco.y + marco.h; gy += 22) {
      const cerca = Math.max(0, 1 - Math.abs(gy - y) / 90);
      if (cerca <= 0) continue;
      ctx.fillStyle = `rgba(125, 211, 252, ${0.55 * cerca})`;
      for (let gx = marco.x + 12; gx < marco.x + marco.w; gx += 22) ctx.fillRect(gx - 1, gy - 1, 2, 2);
    }
    // Estela y línea
    const sube = t >= 0.5;
    const estela = ctx.createLinearGradient(0, y + (sube ? 70 : -70), 0, y);
    estela.addColorStop(0, "rgba(56, 189, 248, 0)");
    estela.addColorStop(1, "rgba(56, 189, 248, .28)");
    ctx.fillStyle = estela;
    ctx.fillRect(marco.x, sube ? y : y - 70, marco.w, 70);
    ctx.shadowColor = "#38bdf8";
    ctx.shadowBlur = 18;
    ctx.fillStyle = "#7dd3fc";
    ctx.fillRect(marco.x, y - 1, marco.w, 2);
    ctx.restore();

    esquinasMarco(ctx, marco, "rgba(125, 211, 252, .9)");
    lienzo.raf = requestAnimationFrame(cuadro);
  };
  lienzo.raf = requestAnimationFrame(cuadro);
}

function esquinasMarco(ctx, r, color, largo = 26, grosor = 3, radio = 6) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = grosor;
  ctx.lineCap = "round";
  const l = Math.min(largo, r.w / 3, r.h / 3);
  const { x, y, w, h } = r;
  ctx.beginPath();
  ctx.moveTo(x, y + l); ctx.lineTo(x, y + radio); ctx.quadraticCurveTo(x, y, x + radio, y); ctx.lineTo(x + l, y);
  ctx.moveTo(x + w - l, y); ctx.lineTo(x + w - radio, y); ctx.quadraticCurveTo(x + w, y, x + w, y + radio); ctx.lineTo(x + w, y + l);
  ctx.moveTo(x + w, y + h - l); ctx.lineTo(x + w, y + h - radio); ctx.quadraticCurveTo(x + w, y + h, x + w - radio, y + h); ctx.lineTo(x + w - l, y + h);
  ctx.moveTo(x + l, y + h); ctx.lineTo(x + radio, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - radio); ctx.lineTo(x, y + h - l);
  ctx.stroke();
  ctx.restore();
}

const COLOR_ESTADO = { identificada: "#059669", desconocida: "#d97706", descartada: "#64748b" };

/** Foto con varias personas: cada cara con su número y su nombre (o "Desconocida"). */
function animarVariasCaras(respuesta) {
  detenerAnimacion();
  const img = respuesta.imagen;
  const duracion = reduceMovimiento ? 1 : 900;
  const inicio = performance.now();
  const aLienzo = (x, y) => {
    const { marco } = lienzo;
    return [marco.x + x * marco.w / img.ancho, marco.y + y * marco.h / img.alto];
  };
  const cuadro = (ahora) => {
    const p = Math.min(1, (ahora - inicio) / duracion);
    const suave = 1 - Math.pow(1 - p, 3);
    const { ctx, ancho, alto } = lienzo;
    pintarFoto(0);
    const rects = respuesta.caras.map((c) => {
      const [x0, y0] = aLienzo(c.caja[0], c.caja[1]);
      const [x1, y1] = aLienzo(c.caja[0] + c.caja[2], c.caja[1] + c.caja[3]);
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    });
    // Foco en todas las caras a la vez
    ctx.save();
    ctx.fillStyle = `rgba(6, 18, 46, ${0.45 * suave})`;
    ctx.beginPath();
    ctx.rect(0, 0, ancho, alto);
    rects.forEach((r) => ctx.roundRect(r.x - 5, r.y - 5, r.w + 10, r.h + 10, 10));
    ctx.fill("evenodd");
    ctx.restore();

    respuesta.caras.forEach((c, i) => {
      const r = rects[i];
      const aparicion = Math.max(0, Math.min(1, suave * 1.6 - i * 0.12));
      if (aparicion <= 0) return;
      const color = COLOR_ESTADO[c.estado];
      ctx.save();
      ctx.globalAlpha = aparicion;
      if (c.estado === "descartada") {
        ctx.setLineDash([5, 5]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        ctx.setLineDash([]);
      } else {
        ctx.shadowColor = color;
        ctx.shadowBlur = 12;
        esquinasMarco(ctx, r, color, Math.max(12, r.w * 0.22), 3.5, 8);
        ctx.shadowBlur = 0;
        c.landmarks.forEach(([lx, ly]) => {
          const [px, py] = aLienzo(lx, ly);
          ctx.beginPath();
          ctx.arc(px, py, 2.6, 0, Math.PI * 2);
          ctx.fillStyle = "#fff";
          ctx.fill();
        });
      }
      const nombre = c.estado === "identificada" ? c.id : c.estado === "desconocida" ? "Desconocida" : "No comprobable";
      etiqueta(ctx, r.x, r.y - 8, `${c.numero} · ${nombre}`, true, color);
      ctx.restore();
    });
    if (p < 1) lienzo.raf = requestAnimationFrame(cuadro);
    else lienzo.raf = 0;
  };
  lienzo.raf = requestAnimationFrame(cuadro);
}

/** Detecciones: foco en la cara elegida, esquinas, confianza y 5 puntos. */
function animarDetecciones(respuesta) {
  if (respuesta.multiple) { animarVariasCaras(respuesta); return; }
  detenerAnimacion();
  const detecciones = respuesta.detecciones || [];
  const img = respuesta.imagen;
  const duracion = reduceMovimiento ? 1 : 900;
  const inicio = performance.now();

  const aLienzo = (x, y) => {
    const { marco } = lienzo;
    const sx = marco.w / (img?.ancho || 1), sy = marco.h / (img?.alto || 1);
    return [marco.x + x * sx, marco.y + y * sy];
  };

  const cuadro = (ahora) => {
    const p = Math.min(1, (ahora - inicio) / duracion);
    const suave = 1 - Math.pow(1 - p, 3);
    const { ctx, ancho, alto } = lienzo;
    pintarFoto(0);
    const elegida = detecciones.find((d) => d.elegida);
    const manual = respuesta.seleccion && respuesta.seleccion !== "automatica";
    // Sin puntos clave: el foco es el propio recuadro marcado a mano
    const foco = elegida ? elegida.caja : (respuesta.seleccion === "manual_sin_puntos" ? respuesta.region : null);

    // Foco: oscurece todo menos la cara elegida
    if (foco) {
      const [x0, y0] = aLienzo(foco[0], foco[1]);
      const [x1, y1] = aLienzo(foco[0] + foco[2], foco[1] + foco[3]);
      ctx.save();
      ctx.fillStyle = `rgba(6, 18, 46, ${0.5 * suave})`;
      ctx.beginPath();
      ctx.rect(0, 0, ancho, alto);
      ctx.roundRect(x0 - 6, y0 - 6, x1 - x0 + 12, y1 - y0 + 12, 12);
      ctx.fill("evenodd");
      ctx.restore();
    } else if (detecciones.length === 0 && respuesta.error) {
      ctx.fillStyle = `rgba(127, 29, 29, ${0.35 * suave})`;
      ctx.fillRect(0, 0, ancho, alto);
    }

    detecciones.forEach((d, i) => {
      const [x0, y0] = aLienzo(d.caja[0], d.caja[1]);
      const [x1, y1] = aLienzo(d.caja[0] + d.caja[2], d.caja[1] + d.caja[3]);
      const r = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      const retardo = Math.max(0, Math.min(1, suave * 1.4 - i * 0.15));
      if (retardo <= 0) return;
      // La caja "se cierra" sobre la cara
      const crecer = 1 + (1 - retardo) * 0.35;
      const rc = { x: r.x + r.w / 2 - (r.w * crecer) / 2, y: r.y + r.h / 2 - (r.h * crecer) / 2, w: r.w * crecer, h: r.h * crecer };
      ctx.save();
      ctx.globalAlpha = retardo;
      if (d.elegida) {
        ctx.shadowColor = "#38bdf8";
        ctx.shadowBlur = 14;
        esquinasMarco(ctx, rc, "#7dd3fc", Math.max(14, r.w * 0.22), 3.5, 8);
        ctx.shadowBlur = 0;
        ctx.strokeStyle = "rgba(125, 211, 252, .35)";
        ctx.lineWidth = 1;
        ctx.strokeRect(rc.x, rc.y, rc.w, rc.h);
      } else {
        ctx.setLineDash([5, 5]);
        ctx.strokeStyle = "rgba(255, 255, 255, .65)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(rc.x, rc.y, rc.w, rc.h);
        ctx.setLineDash([]);
      }
      etiqueta(ctx, rc.x, rc.y - 8, d.elegida ? `${manual ? "Marcada a mano" : "Cara"} · ${pct(d.confianza)}` : `Ignorada · ${pct(d.confianza)}`, d.elegida);
      // 5 puntos clave, uno tras otro
      if (d.elegida) {
        d.landmarks.forEach(([lx, ly], k) => {
          const aparicion = Math.max(0, Math.min(1, (p - 0.35 - k * 0.08) / 0.2));
          if (aparicion <= 0) return;
          const [px, py] = aLienzo(lx, ly);
          ctx.beginPath();
          ctx.arc(px, py, 3.6 * aparicion + 0.5, 0, Math.PI * 2);
          ctx.fillStyle = "#38bdf8";
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = "#fff";
          ctx.stroke();
        });
        if (p > 0.85) { // líneas tenues ojos-nariz-boca
          const pts = d.landmarks.map(([lx, ly]) => aLienzo(lx, ly));
          ctx.globalAlpha = retardo * (p - 0.85) / 0.15 * 0.6;
          ctx.strokeStyle = "#7dd3fc";
          ctx.lineWidth = 1;
          ctx.beginPath();
          [[0, 1], [0, 2], [1, 2], [2, 3], [2, 4], [3, 4]].forEach(([a, b]) => { ctx.moveTo(...pts[a]); ctx.lineTo(...pts[b]); });
          ctx.stroke();
        }
      }
      ctx.restore();
    });

    if (respuesta.seleccion === "manual_sin_puntos" && respuesta.region) {
      const [x0, y0] = aLienzo(respuesta.region[0], respuesta.region[1]);
      const [x1, y1] = aLienzo(respuesta.region[0] + respuesta.region[2], respuesta.region[1] + respuesta.region[3]);
      ctx.save();
      ctx.globalAlpha = suave;
      esquinasMarco(ctx, { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, "#fbbf24", Math.max(14, (x1 - x0) * 0.22), 3.5, 8);
      etiqueta(ctx, x0, y0 - 8, "Marcada a mano · sin puntos", true, "#d97706");
      ctx.restore();
    }

    if (p < 1) lienzo.raf = requestAnimationFrame(cuadro);
    else lienzo.raf = 0;
  };
  lienzo.raf = requestAnimationFrame(cuadro);
}

/* ------------------------------------------------------------------ */
/* Marcar la cara a mano                                               */
/* ------------------------------------------------------------------ */
const aImagen = (cx, cy) => {
  const { marco } = lienzo;
  const ancho = estado.ultimaRespuesta?.imagen?.ancho || estado.bitmap.width;
  const alto = estado.ultimaRespuesta?.imagen?.alto || estado.bitmap.height;
  return [(cx - marco.x) * ancho / marco.w, (cy - marco.y) * alto / marco.h];
};
const aLienzoDesdeImagen = (x, y) => {
  const { marco } = lienzo;
  const ancho = estado.ultimaRespuesta?.imagen?.ancho || estado.bitmap.width;
  const alto = estado.ultimaRespuesta?.imagen?.alto || estado.bitmap.height;
  return [marco.x + x * marco.w / ancho, marco.y + y * marco.h / alto];
};

function entrarSeleccion() {
  if (!estado.bitmap || estado.ocupado) return;
  detenerAnimacion();
  estado.seleccion = { inicio: null, actual: null, rect: null };
  $("#lienzo").classList.add("seleccionando");
  $("#escenario-estado").textContent = "Arrastra un recuadro alrededor de la cara";
  mostrarBotones({ cancelar: true });
  dibujarSeleccion();
}

function salirSeleccion(redibujar = true) {
  if (!estado.seleccion) return;
  estado.seleccion = null;
  $("#lienzo")?.classList.remove("seleccionando");
  $("#escenario-estado").textContent = "";
  if (redibujar && estado.ultimaRespuesta) {
    animarDetecciones(estado.ultimaRespuesta);
    mostrarBotones({ otra: true, manual: puedeMarcar(estado.ultimaRespuesta) });
  }
}

function rectSeleccion() {
  const sel = estado.seleccion;
  if (sel.inicio && sel.actual) {
    const [ax, ay] = sel.inicio, [bx, by] = sel.actual;
    return { x: Math.min(ax, bx), y: Math.min(ay, by), w: Math.abs(bx - ax), h: Math.abs(by - ay) };
  }
  return sel.rect;
}

function dibujarSeleccion() {
  const { ctx } = lienzo;
  pintarFoto(0.45);
  // Caras que encontró el detector: se pueden pulsar para elegirlas
  for (const d of estado.ultimaRespuesta?.detecciones || []) {
    const [x0, y0] = aLienzoDesdeImagen(d.caja[0], d.caja[1]);
    const [x1, y1] = aLienzoDesdeImagen(d.caja[0] + d.caja[2], d.caja[1] + d.caja[3]);
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = "rgba(255, 255, 255, .7)";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    ctx.restore();
    etiqueta(ctx, x0, y0 - 8, "Pulsa para elegir", false);
  }
  const r = rectSeleccion();
  if (r && r.w > 2 && r.h > 2) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, lienzo.ancho, lienzo.alto);
    ctx.rect(r.x, r.y, r.w, r.h);
    ctx.fillStyle = "rgba(6, 18, 46, .45)";
    ctx.fill("evenodd");
    ctx.drawImage(estado.bitmap, ...(() => {
      const [ix, iy] = aImagen(r.x, r.y), [jx, jy] = aImagen(r.x + r.w, r.y + r.h);
      const k = estado.bitmap.width / (estado.ultimaRespuesta?.imagen?.ancho || estado.bitmap.width);
      return [ix * k, iy * k, (jx - ix) * k, (jy - iy) * k, r.x, r.y, r.w, r.h];
    })());
    ctx.restore();
    esquinasMarco(ctx, r, "#fbbf24", Math.max(12, Math.min(r.w, r.h) * 0.25), 3, 6);
    etiqueta(ctx, r.x, r.y - 8, "Cara marcada a mano", true, "#d97706");
  }
}

function prepararSeleccion() {
  const canvas = $("#lienzo");
  const punto = (e) => {
    const caja = canvas.getBoundingClientRect();
    const { marco } = lienzo;
    const x = Math.min(Math.max(e.clientX - caja.left, marco.x), marco.x + marco.w);
    const y = Math.min(Math.max(e.clientY - caja.top, marco.y), marco.y + marco.h);
    return [x, y];
  };
  canvas.addEventListener("pointerdown", (e) => {
    if (!estado.seleccion) return;
    e.preventDefault();
    e.stopPropagation();
    canvas.setPointerCapture(e.pointerId);
    estado.seleccion.inicio = punto(e);
    estado.seleccion.actual = punto(e);
    dibujarSeleccion();
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!estado.seleccion?.inicio) return;
    estado.seleccion.actual = punto(e);
    dibujarSeleccion();
  });
  canvas.addEventListener("pointerup", (e) => {
    const sel = estado.seleccion;
    if (!sel?.inicio) return;
    const r = rectSeleccion();
    sel.inicio = sel.actual = null;
    if (r.w < 6 && r.h < 6) {
      // Un clic: ¿sobre una cara detectada?
      const [ix, iy] = aImagen(r.x, r.y);
      const d = (estado.ultimaRespuesta?.detecciones || []).find((c) =>
        ix >= c.caja[0] && ix <= c.caja[0] + c.caja[2] && iy >= c.caja[1] && iy <= c.caja[1] + c.caja[3]);
      if (d) {
        const [x0, y0] = aLienzoDesdeImagen(d.caja[0], d.caja[1]);
        const [x1, y1] = aLienzoDesdeImagen(d.caja[0] + d.caja[2], d.caja[1] + d.caja[3]);
        sel.rect = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      }
    } else if (r.w >= 12 && r.h >= 12) {
      sel.rect = r;
    }
    dibujarSeleccion();
    if (sel.rect) {
      $("#escenario-estado").textContent = "¿Es esta la cara? Pulsa \"Usar esta cara\" o vuelve a marcar";
      mostrarBotones({ usar: true, cancelar: true });
    }
  });
  canvas.addEventListener("click", (e) => { if (estado.seleccion) e.stopPropagation(); });
}

function usarSeleccion() {
  const r = estado.seleccion?.rect;
  if (!r) return;
  const [x0, y0] = aImagen(r.x, r.y);
  const [x1, y1] = aImagen(r.x + r.w, r.y + r.h);
  estado.caja = [x0, y0, x1 - x0, y1 - y0].map((v) => Math.round(v));
  estado.cajaOrigen = "manual";
  salirSeleccion(false);
  ejecutar();
}

function etiqueta(ctx, x, y, texto, destacada, color = "#0ea5e9") {
  ctx.save();
  ctx.font = "700 12px 'Plus Jakarta Sans', sans-serif";
  const w = ctx.measureText(texto).width + 16;
  const h = 22;
  const yy = Math.max(4, y - h);
  const xx = Math.min(Math.max(4, x), lienzo.ancho - w - 4);
  ctx.fillStyle = destacada ? color : "rgba(15, 27, 61, .75)";
  ctx.beginPath();
  ctx.roundRect(xx, yy, w, h, 7);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.fillText(texto, xx + 8, yy + h / 2 + 0.5);
  ctx.restore();
}

/* ------------------------------------------------------------------ */
/* Pipeline (columna derecha)                                          */
/* ------------------------------------------------------------------ */
function reiniciarPipeline() {
  $$(".paso").forEach((p) => {
    p.className = "paso";
    $(".paso__tiempo", p).textContent = "";
    const mini = $(".paso__miniatura", p);
    if (mini) mini.hidden = true;
    const codigo = $(".paso__codigo", p);
    if (codigo) codigo.hidden = true;
  });
  const det = {
    carga: "Se valida tamaño y formato",
    deteccion: "Red YuNet: cajas y 5 puntos clave",
    alineado: "Ojos horizontales, 112×112, nitidez",
    extraccion: "La cara convertida en números",
  };
  for (const [paso, texto] of Object.entries(det)) $(`.paso[data-paso="${paso}"] .paso__detalle`).textContent = texto;
  const m = MODOS[estado.modo];
  if (m) $("#paso-comparacion-detalle").textContent = m.comparacion[1];
}

function marcarPaso(paso, clase, detalle, tiempo) {
  const nodo = $(`.paso[data-paso="${paso}"]`);
  nodo.className = `paso ${clase}`;
  if (detalle != null) $(".paso__detalle", nodo).textContent = detalle;
  if (tiempo != null) $(".paso__tiempo", nodo).textContent = `${Math.max(1, Math.round(tiempo))} ms`;
}

function dibujarCodigo(canvas, valores, alto) {
  canvas.hidden = false;
  const dpr = window.devicePixelRatio || 1;
  const ancho = canvas.clientWidth || canvas.parentElement.clientWidth || 240;
  canvas.width = ancho * dpr;
  canvas.height = alto * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, ancho, alto);
  // Si hay más valores que píxeles (LBP: 2891), se agrupan por máximo absoluto
  const n = Math.min(valores.length, Math.floor(ancho / 2));
  const grupo = valores.length / n;
  const barras = Array.from({ length: n }, (_, i) => {
    const trozo = valores.slice(Math.floor(i * grupo), Math.floor((i + 1) * grupo));
    return trozo.reduce((a, v) => (Math.abs(v) > Math.abs(a) ? v : a), 0);
  });
  const maximo = Math.max(...barras.map(Math.abs)) || 1;
  const conSigno = barras.some((v) => v < 0);
  const ab = ancho / n;
  barras.forEach((v, i) => {
    const h = Math.max(1, (Math.abs(v) / maximo) * (conSigno ? alto / 2 - 1 : alto - 1));
    ctx.fillStyle = v >= 0 ? "#2563eb" : "#38bdf8";
    const y = conSigno ? (v >= 0 ? alto / 2 - h : alto / 2) : alto - h;
    ctx.fillRect(i * ab, y, Math.max(1, ab - 0.6), h);
  });
}

async function animarPipeline(respuesta, operacion) {
  const t = respuesta.tiempos_ms || {};
  const err = respuesta.error;
  const pasoError = err ? (ERRORES[err.error]?.[0] || "comparacion") : null;
  const pausa = reduceMovimiento ? 0 : 280;
  const det = respuesta.detecciones || [];
  const elegida = det.find((d) => d.elegida);

  const detalles = {
    carga: respuesta.imagen ? `${respuesta.imagen.ancho}×${respuesta.imagen.alto} px` : null,
    deteccion: respuesta.seleccion === "manual_sin_puntos" ? "Marcada a mano · sin puntos clave"
      : respuesta.seleccion === "manual" && elegida ? `Marcada a mano · confianza ${pct(elegida.confianza)}`
      : det.length === 0 ? "Ninguna cara encontrada"
      : `${det.length} cara${det.length > 1 ? "s" : ""}${elegida ? ` · confianza ${pct(elegida.confianza)}` : ""}${det.length > 1 && elegida ? " · se usa la mayor" : ""}`,
    alineado: respuesta.nitidez == null ? null
      : respuesta.seleccion === "manual_sin_puntos" ? `Recorte sin alinear · nitidez ${Math.round(respuesta.nitidez)}`
      : `112×112 · nitidez ${Math.round(respuesta.nitidez)} ✓`,
    extraccion: respuesta.template ? `${respuesta.template.dimension} números` : null,
    comparacion: null,
  };
  const r = respuesta.resultado;
  if (r?.multiple) {
    const comprobables = r.identificadas + r.desconocidas;
    detalles.deteccion = `${respuesta.caras.length} caras · se comprueban todas`;
    detalles.alineado = `${comprobables} cara${comprobables === 1 ? "" : "s"} alineada${comprobables === 1 ? "" : "s"}${r.descartadas ? ` · ${r.descartadas} descartada${r.descartadas > 1 ? "s" : ""}` : ""}`;
    detalles.extraccion = `${comprobables} template${comprobables === 1 ? "" : "s"} de ${respuesta.template.dimension} números`;
    detalles.comparacion = `${comprobables} cara${comprobables === 1 ? "" : "s"} × ${r.personas} personas`;
  } else if (r) {
    if (estado.modo === "verificar") detalles.comparacion = `Con ${r.id}: ${pct(r.puntuacion)}`;
    if (estado.modo === "identificar") detalles.comparacion = `Con ${r.personas} persona${r.personas === 1 ? "" : "s"}`;
    if (estado.modo === "inscribir") detalles.comparacion = `Guardado · ${r.muestras} muestra${r.muestras > 1 ? "s" : ""}`;
  }

  let deteccionPintada = false;
  for (const paso of PASOS) {
    if (paso === "comparacion" && operacion === "analizar" && !err) {
      marcarPaso(paso, "", "Pendiente de tu confirmación", null);
      break;
    }
    if (paso === pasoError) {
      marcarPaso(paso, "error", ERRORES[err.error]?.[1] || err.mensaje, t[paso]);
      break;
    }
    marcarPaso(paso, "activo", null, null);
    if (paso === "deteccion") { animarDetecciones(respuesta); deteccionPintada = true; }
    await esperar(pausa);
    marcarPaso(paso, "hecho", detalles[paso], t[paso]);
    if (paso === "alineado" && respuesta.cara_alineada) {
      const mini = $(`.paso[data-paso="alineado"] .paso__miniatura`);
      mini.src = respuesta.cara_alineada;
      mini.hidden = false;
    }
    if (paso === "extraccion" && respuesta.template) {
      dibujarCodigo($(`.paso[data-paso="extraccion"] .paso__codigo`), respuesta.template.valores, 26);
    }
  }
  // Si falló antes de llegar a pintar la detección (o en ella), se para el escaneo y se
  // enseña lo que haya: cajas de varias caras, o la foto en rojo si no hay ninguna.
  if (!deteccionPintada) animarDetecciones(respuesta);
}

/* ------------------------------------------------------------------ */
/* Ejecutar una operación                                              */
/* ------------------------------------------------------------------ */
async function ejecutar({ operacion = null, rapido = false } = {}) {
  if (estado.ocupado || !estado.archivo) return;
  const m = MODOS[estado.modo];
  const nombre = $("#nombre").value.trim();
  if (m.pideNombre && !nombre) { pedirNombre(); return; }
  // Inscribir va en dos pasos: primero se analiza y se pide confirmación
  const op = operacion || (estado.modo === "inscribir" ? "analizar" : estado.modo);

  salirSeleccion(false);
  estado.ocupado = true;
  mostrarBotones({});
  reiniciarPipeline();
  pintarResultadoVacio("Analizando…");
  ajustarLienzo();
  animarEscaneo();
  marcarPaso("carga", "activo");
  const estadoEscenario = $("#escenario-estado");
  estadoEscenario.replaceChildren(el("span", { class: "girando" }), "Buscando caras…");

  const parametros = new URLSearchParams();
  if (op === "verificar" || op === "inscribir") parametros.set("id", nombre);
  if (estado.caja) parametros.set("caja", estado.caja.join(","));
  const consulta = parametros.toString();
  const url = `/api/${op}` + (consulta ? `?${consulta}` : "");
  let respuesta;
  try {
    [respuesta] = await Promise.all([
      api(url, {
        method: "POST",
        headers: { "Content-Type": estado.archivo.type || "application/octet-stream", "X-JumeX-Lens": "1" },
        body: estado.archivo,
      }),
      esperar(reduceMovimiento || rapido ? 0 : 1400), // que dé tiempo a ver el escaneo
    ]);
  } catch (e) {
    respuesta = { ok: false, detecciones: [], error: { error: "red", mensaje: `No se pudo contactar con el servidor (${e.message}).` } };
  }
  // Una cara detectada y confirmada se envía como caja, pero no la ha marcado el usuario
  if (estado.cajaOrigen === "confirmada" && respuesta.seleccion === "manual") respuesta.seleccion = "automatica";
  estado.ultimaRespuesta = respuesta;
  estado.ultimaOperacion = op;
  estadoEscenario.textContent = "";

  await animarPipeline(respuesta, op);
  if (op === "analizar" && respuesta.ok) pintarConfirmacion(respuesta, nombre);
  else pintarResultado(respuesta, nombre);
  if (matchMedia("(max-width: 960px)").matches) {
    $("#resultado").scrollIntoView({ behavior: reduceMovimiento ? "auto" : "smooth", block: "start" });
  }
  estado.ocupado = false;
  mostrarBotones({ otra: true, manual: puedeMarcar(respuesta) });

  if (op === "inscribir" && respuesta.ok) {
    // La cara elegida valía para esta inscripción; la siguiente operación vuelve a detectar todas
    estado.caja = null;
    estado.cajaOrigen = null;
    estado.inscritoReciente = respuesta.resultado.id;
    await recargar();
  }
}

/* ------------------------------------------------------------------ */
/* Tarjetas de resultado                                               */
/* ------------------------------------------------------------------ */
function pintarResultadoVacio(texto = null) {
  let simbolo;
  if (texto) simbolo = el("span", { class: "girando--azul" });
  else {
    simbolo = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    simbolo.setAttribute("viewBox", "0 0 24 24");
    simbolo.innerHTML = '<circle cx="12" cy="12" r="9"/><path d="M12 8v4l2.5 2.5"/>';
  }
  $("#resultado").replaceChildren(el("div", { class: "resultado__vacio" }, simbolo,
    el("p", { text: texto || "El resultado aparecerá aquí" })));
}

function veredicto(tipo, antetitulo, titulo, subtitulo) {
  return el("div", { class: `veredicto veredicto--${tipo}` },
    el("div", { class: "veredicto__icono" }, icono({ si: "si", no: "no", duda: "duda", info: "persona", aviso: "aviso" }[tipo] || "si")),
    el("div", {},
      el("p", { class: "veredicto__antetitulo", text: antetitulo }),
      el("h3", { text: titulo }),
      subtitulo ? el("p", { text: subtitulo }) : null));
}

function figuraCara(src, titulo, nombre) {
  const marco = el("div", { class: "cara__marco" });
  if (src) marco.append(el("img", { src, alt: nombre || titulo }));
  else marco.append(el("div", { class: "iniciales", text: nombre ? iniciales(nombre) : "?" }));
  return el("figure", { class: "cara" }, marco, el("figcaption", {}, nombre ? el("b", { text: nombre }) : null, titulo));
}

function medidor(puntuacion, umbral, tipo) {
  const relleno = el("div", { class: "medidor__relleno" });
  const umbralNodo = el("div", { class: "medidor__umbral" }, el("span", { text: `umbral ${pct(umbral)}` }));
  umbralNodo.style.left = `${umbral * 100}%`;
  requestAnimationFrame(() => requestAnimationFrame(() => { relleno.style.width = `${puntuacion * 100}%`; }));
  return el("div", { class: `medidor medidor--${tipo}` },
    el("div", { class: "medidor__cabecera" }, el("span", { text: "Similitud" }), el("b", { text: pct(puntuacion) })),
    el("div", { class: "medidor__pista" }, relleno, umbralNodo),
    el("div", { class: "medidor__escala" }, el("span", { text: "0% · nada parecidas" }), el("span", { text: "100% · idénticas" })));
}

function listaCandidatos(candidatos, umbral, titulo = "Los más parecidos") {
  if (!candidatos?.length) return null;
  return el("div", { class: "candidatos" },
    el("h4", { text: titulo }),
    candidatos.map((c) => {
      const barra = el("i");
      barra.style.width = `${c.puntuacion * 100}%`;
      return el("div", { class: `candidato${c.puntuacion >= umbral ? " supera" : ""}` },
        c.foto ? el("img", { src: c.foto, alt: "" }) : el("span", { class: "iniciales", text: iniciales(c.id) }),
        el("span", { class: "candidato__nombre", text: c.id }),
        el("span", { class: "candidato__barra" }, barra),
        el("span", { class: "candidato__valor", text: pct(c.puntuacion) }));
    }));
}

function explicacion(...partes) {
  return el("p", { class: "explicacion" }, ...partes);
}

/** Inscribir, paso 1: enseñar la cara encontrada y pedir confirmación antes de guardar. */
function pintarConfirmacion(respuesta, nombre) {
  const elegida = (respuesta.detecciones || []).find((d) => d.elegida);
  const sinPuntos = respuesta.seleccion === "manual_sin_puntos";
  const contenido = el("div", { class: "aparece" },
    veredicto("info", "Revisa antes de guardar", `¿Es esta la cara de ${nombre}?`,
      "Comprueba que el recuadro está sobre la cara correcta. No se guarda nada hasta que confirmes."),
    el("div", { class: "guardado" },
      figuraCara(respuesta.retrato || respuesta.cara_alineada, "La cara encontrada"),
      el("div", { class: "guardado__datos" },
        el("h4", { text: "Si está bien" }),
        el("p", { text: `Se guardará su template (${respuesta.template.dimension} números) con el nombre "${nombre}".` }),
        el("h4", { text: "Si no es la cara", style: null }),
        el("p", { text: "Márcala tú arrastrando un recuadro sobre la foto." }))),
    sinPuntos ? notaManual(respuesta) : null,
    el("div", { class: "acciones" },
      el("button", {
        class: "boton boton--principal", onclick: () => {
          // Se inscribe exactamente la cara revisada
          const r = elegida ? elegida.caja : respuesta.region;
          if (r) {
            estado.caja = r;
            if (respuesta.seleccion === "automatica") estado.cajaOrigen = "confirmada";
          }
          ejecutar({ operacion: "inscribir", rapido: true });
        },
      }, icono("si"), `Sí, inscribir a ${nombre}`),
      el("button", { class: "boton boton--secundario", onclick: entrarSeleccion }, "No, la marco yo")),
  );
  $("#resultado").replaceChildren(contenido);
}

/** Identificar con varias personas: una fila por cara. */
function pintarVariasCaras(respuesta) {
  const r = respuesta.resultado;
  const partes = [
    r.identificadas && `${r.identificadas} identificada${r.identificadas > 1 ? "s" : ""}`,
    r.desconocidas && `${r.desconocidas} desconocida${r.desconocidas > 1 ? "s" : ""}`,
    r.descartadas && `${r.descartadas} sin poder comprobar`,
  ].filter(Boolean);
  const filas = respuesta.caras.map((c) => {
    const fotoBD = c.estado === "identificada"
      ? (c.foto ? el("img", { src: c.foto, alt: c.id }) : el("span", { class: "iniciales", text: iniciales(c.id) }))
      : el("span", { class: "iniciales", text: "?" });
    let texto, detalle;
    if (c.estado === "identificada") {
      texto = el("p", {}, "Esta persona es ", el("b", { text: c.id }));
      detalle = `Similitud ${pct(c.puntuacion)} (umbral ${pct(r.umbral)})`;
    } else if (c.estado === "desconocida") {
      texto = el("p", {}, "A esta persona ", el("b", { text: "no la tengo guardada" }));
      const mejor = c.candidatos?.[0]; // solo si supera el umbral de parecido
      detalle = mejor ? `Se parece algo a ${mejor.id} (${pct(mejor.puntuacion)}), pero no lo bastante`
        : c.puntuacion != null ? `Nadie se le parece: la más cercana solo llega al ${pct(c.puntuacion)}`
        : "La base de datos está vacía";
    } else {
      texto = el("p", {}, el("b", { text: "No puedo comprobar esta cara" }));
      detalle = ERRORES[c.error?.error]?.[1] || c.error?.mensaje || "";
    }
    return el("li", { class: `grupo__fila grupo__fila--${c.estado}` },
      el("span", { class: "grupo__numero", text: c.numero }),
      el("img", { class: "grupo__cara", src: c.retrato, alt: `Cara ${c.numero}` }),
      el("span", { class: "grupo__flecha" }, icono(c.estado === "identificada" ? "igual" : c.estado === "desconocida" ? "distinto" : "aviso")),
      el("span", { class: "grupo__bd" }, fotoBD),
      el("div", { class: "grupo__texto" }, texto, el("small", { text: detalle })),
      c.estado === "desconocida"
        ? el("button", { class: "boton boton--secundario boton--pequeno", onclick: () => inscribirCara(c), text: "Inscribirla" })
        : null);
  });
  $("#resultado").replaceChildren(el("div", { class: "aparece" },
    veredicto(r.identificadas ? "si" : "duda", "Varias personas en la foto",
      `He encontrado ${respuesta.caras.length} personas`, partes.join(" · ")),
    el("ol", { class: "grupo" }, filas),
    el("p", { class: "explicacion" }, el("b", { text: "¿Cómo? " }),
      `Cada cara se alinea y se convierte en su propio template, y se compara con las ${r.personas} personas de la base de datos. Si alguna sale mal, márcala a mano para comprobarla sola.`)));
}

/** Desde una foto de grupo: inscribir a una de las caras desconocidas. */
function inscribirCara(cara) {
  estado.caja = cara.caja;
  estado.cajaOrigen = "confirmada";
  $("#nombre").value = "";
  cambiarModo("inscribir", { conservarFoto: true });
  window.scrollTo({ top: $("#vista-operacion").offsetTop - 16, behavior: reduceMovimiento ? "auto" : "smooth" });
  pedirNombre();
  avisar(`Escribe el nombre de la persona ${cara.numero} y pulsa Inscribir`);
}

function notaManual(respuesta) {
  if (respuesta.seleccion === "manual") {
    return el("p", { class: "explicacion" }, el("b", { text: "Cara marcada a mano. " }),
      "Se buscó la cara dentro de tu recuadro y se alineó con sus 5 puntos, igual que en la detección automática.");
  }
  if (respuesta.seleccion === "manual_sin_puntos") {
    return el("p", { class: "explicacion explicacion--aviso" }, el("b", { text: "Ojo: cara sin alinear. " }),
      "Dentro del recuadro no se encontraron los puntos de la cara, así que se usa el recorte tal cual (como los métodos _sd). La comparación será menos fiable.");
  }
  return null;
}

function pintarResultado(respuesta, nombre) {
  const caja = $("#resultado");
  const contenido = el("div", { class: "aparece" });
  // append(null) escribiría "null": las piezas opcionales (lista vacía, sin candidato...) se saltan
  const anadir = (...nodos) => contenido.append(...nodos.filter((n) => n != null));

  if (!respuesta.ok) {
    const e = respuesta.error || { error: "desconocido", mensaje: "Error desconocido" };
    const [, titulo, ayuda] = ERRORES[e.error] || [null, "Algo ha fallado", null];
    anadir(
      veredicto(e.error === "integridad_bd" ? "no" : "aviso", e.error === "red" ? "Sin conexión" : "No se ha podido completar", titulo, ayuda || e.mensaje),
      ayuda && e.mensaje ? explicacion(el("b", { text: "Detalle técnico: " }), `${e.mensaje} (código ${e.error})`) : null,
    );
    if (respuesta.retrato) anadir(el("div", { class: "caras" }, figuraCara(respuesta.retrato, "La que has subido", "Tu foto"), el("div"), el("div")));
    if (puedeMarcar(respuesta) && ["sin_cara", "varias_caras", "cara_pequena", "imagen_borrosa"].includes(e.error)) {
      anadir(el("div", { class: "acciones" },
        el("button", { class: "boton boton--principal", onclick: entrarSeleccion }, "Marcar la cara a mano")));
    }
    caja.replaceChildren(contenido);
    return;
  }

  const r = respuesta.resultado;
  if (estado.modo === "verificar") {
    const si = r.aceptado;
    // El usuario ve el veredicto pero no la foto guardada en la BD (r.foto llega sin él)
    anadir(
      veredicto(si ? "si" : "no", si ? "Identidad confirmada" : "Identidad rechazada",
        si ? `Sí, es ${r.id}` : `No, no es ${r.id}`,
        si ? "La cara de la foto coincide con la guardada." : "La cara de la foto no coincide con la guardada."),
      estado.admin
        ? el("div", { class: "caras" },
            figuraCara(respuesta.retrato, "La que has subido", "Tu foto"),
            el("div", { class: `conector conector--${si ? "si" : "no"}` }, el("span", {}, icono(si ? "igual" : "distinto")), pct(r.puntuacion)),
            figuraCara(r.foto, "En la base de datos", r.id))
        : null,
      medidor(r.puntuacion, r.umbral, si ? "si" : "no"),
      explicacion(
        el("b", { text: "¿Por qué? " }),
        si ? `La similitud (${pct(r.puntuacion)}) supera el umbral (${pct(r.umbral)}), así que el sistema acepta que es ${r.id}.`
           : `La similitud (${pct(r.puntuacion)}) no llega al umbral (${pct(r.umbral)}), así que el sistema rechaza que sea ${r.id}.`),
    );
  } else if (estado.modo === "identificar") {
    if (r.multiple) { pintarVariasCaras(respuesta); return; }
    if (r.personas === 0) {
      anadir(veredicto("duda", "Base de datos vacía", "No hay nadie con quien comparar", "Inscribe a alguien primero en la pestaña Inscribir."));
    } else if (!estado.admin) {
      // Vista de usuario: solo el nombre (o desconocido), sin foto de la BD ni candidatos
      if (r.identificado) {
        anadir(
          veredicto("si", "Persona identificada", `Es ${r.id}`,
            `La foto coincide con una persona de la base de datos (${pct(r.puntuacion)} de similitud).`),
          el("div", { class: "caras caras--sola" }, figuraCara(respuesta.retrato, "La que has subido", "Tu foto")),
          medidor(r.puntuacion, r.umbral, "si"));
      } else {
        anadir(
          veredicto("duda", "Persona desconocida", "No está en la base de datos",
            "La foto no coincide con nadie guardado."),
          el("div", { class: "caras caras--sola" }, figuraCara(respuesta.retrato, "La que has subido", "Tu foto")),
          medidor(r.puntuacion, r.umbral, "no"));
      }
    } else if (r.identificado) {
      // Solo llegan los que superan el umbral de parecido; el identificado no se repite
      const otros = (r.candidatos || []).filter((c) => c.id !== r.id);
      anadir(
        veredicto("si", "Persona identificada", `Esta persona es ${r.id}`, `La más parecida de ${r.personas} personas, y supera el umbral.`),
        el("div", { class: "caras" },
          figuraCara(respuesta.retrato, "La que has subido", "Tu foto"),
          el("div", { class: "conector conector--si" }, el("span", {}, icono("igual")), pct(r.puntuacion)),
          figuraCara(r.foto, "En la base de datos", r.id)),
        medidor(r.puntuacion, r.umbral, "si"),
        otros.length
          ? listaCandidatos(otros, r.umbral, "También se le parecen")
          : r.personas > 1 ? explicacion(el("b", { text: "Nadie más se le parece. " }),
            `Las otras ${r.personas - 1} personas de la base de datos quedan por debajo del ${pct(r.parecido)}: lo que puntúan dos desconocidos cualesquiera.`) : null,
      );
    } else {
      const mejor = r.candidatos?.[0]; // solo si supera el umbral de parecido
      anadir(
        veredicto("duda", "Persona desconocida", "No está en la base de datos",
          mejor ? `Nadie supera el umbral. La más parecida es ${mejor.id}, pero solo al ${pct(mejor.puntuacion)}.`
                : `Nadie se le parece: la persona más cercana solo llega al ${pct(r.puntuacion)}.`),
        mejor ? el("div", { class: "caras" },
          figuraCara(respuesta.retrato, "La que has subido", "Tu foto"),
          el("div", { class: "conector conector--no" }, el("span", {}, icono("distinto")), pct(mejor.puntuacion)),
          figuraCara(mejor.foto, "La más parecida", mejor.id)) : null,
        medidor(r.puntuacion, r.umbral, "no"),
        listaCandidatos(r.candidatos, r.umbral, "Se le parecen algo"),
        explicacion(el("b", { text: "¿Por qué? " }),
          `Para identificar a alguien su similitud tiene que superar el umbral (${pct(r.umbral)}). Si no, es más seguro decir "no lo conozco" que arriesgarse a confundirlo con otra persona.`,
          mejor ? null : ` Solo se enseña como "parecido" a quien pase del ${pct(r.parecido)}; por debajo, la similitud es la de dos desconocidos.`),
      );
    }
  } else if (estado.modo === "inscribir") {
    const nueva = r.nueva_persona;
    const codigo = el("canvas");
    anadir(
      veredicto("si", nueva ? "Persona inscrita" : "Muestra añadida",
        nueva ? `${r.id}, bienvenido/a` : `Nueva foto para ${r.id}`,
        nueva ? "Ya está en la base de datos: prueba a verificarla o identificarla con otra foto."
              : `Ahora tiene ${r.muestras} muestras. Se compara con la más parecida.`),
      el("div", { class: "guardado" },
        figuraCara(r.foto || respuesta.retrato, nueva ? "Retrato para la galería" : "Retrato de la galería"),
        el("div", { class: "guardado__datos" },
          el("h4", { text: "Lo que se guarda en bd.csv" }),
          codigo,
          el("p", { text: `${respuesta.template.dimension} números (el template) + una firma HMAC que detecta cualquier cambio. La foto no se usa para comparar.` }))),
      el("div", { class: "acciones" },
        el("button", { class: "boton boton--secundario", text: "Verificarla ahora", onclick: () => { $("#nombre").value = r.id; cambiarModo("verificar"); } }),
        el("button", { class: "boton boton--enlace", text: "Ver en la base de datos →", onclick: () => cambiarModo("bd") })),
    );
    const nota = notaManual(respuesta);
    if (nota) contenido.insertBefore(nota, contenido.lastChild);
    caja.replaceChildren(contenido);
    requestAnimationFrame(() => dibujarCodigo(codigo, respuesta.template.valores, 54));
    return;
  }
  const nota = notaManual(respuesta);
  if (nota) anadir(nota);
  caja.replaceChildren(contenido);
}

/* ------------------------------------------------------------------ */
/* Base de datos                                                       */
/* ------------------------------------------------------------------ */
function pintarBD() {
  const s = estado.sistema;
  const insignias = $("#bd-insignias");
  insignias.replaceChildren();
  if (s) {
    const muestras = estado.personas.reduce((a, p) => a + p.muestras, 0);
    insignias.append(
      el("span", { class: `insignia ${s.integridad === "ok" ? "insignia--ok" : "insignia--mal"}` },
        icono("escudo"), s.integridad === "ok" ? "Integridad HMAC verificada" : "Integridad rota"),
      el("span", { class: "insignia", text: `${estado.personas.length} personas · ${muestras} muestras` }),
      el("span", { class: "insignia", text: `Template: ${s.dimension} números` }));
  }

  const filtro = $("#buscador").value.trim().toLocaleLowerCase("es");
  const visibles = estado.personas.filter((p) => p.id.toLocaleLowerCase("es").includes(filtro));
  const rejilla = $("#rejilla-bd");
  rejilla.replaceChildren(...visibles.map((p, i) => {
    const marco = el("div", { class: "cara__marco" });
    marco.append(p.foto ? el("img", { src: p.foto, alt: "", loading: "lazy" }) : el("div", { class: "iniciales", text: iniciales(p.id) }));
    const tarjeta = el("div", { class: `persona${p.id === estado.inscritoReciente ? " nueva" : ""}` },
      el("button", {
        class: "persona__principal",
        title: `Ver la ficha de ${p.id}`,
        onclick: () => abrirFicha(p.id),
      },
        marco,
        el("span", { class: "persona__nombre", text: p.id }),
        el("span", { class: "persona__meta" },
          el("span", { text: `${p.muestras} muestra${p.muestras > 1 ? "s" : ""}` }),
          el("span", { class: "persona__accion", text: "Ver ficha →" }))),
      el("button", {
        class: "persona__borrar", title: `Eliminar a ${p.id}`, "aria-label": `Eliminar a ${p.id}`,
        onclick: () => eliminarPersona(p),
      }, icono("papelera")));
    tarjeta.style.setProperty("--i", Math.min(i, 40));
    return tarjeta;
  }));

  const vacia = $("#bd-vacia");
  vacia.hidden = visibles.length > 0;
  vacia.textContent = estado.personas.length === 0
    ? "La base de datos está vacía. Inscribe a alguien en la pestaña Inscribir."
    : `Nadie coincide con "${$("#buscador").value}".`;
}

/* ------------------------------------------------------------------ */
/* Ficha de una persona: sus fotos y edición                           */
/* ------------------------------------------------------------------ */
const ficha = { id: null, datos: null, renombrando: false, anadiendo: null };

async function abrirFicha(id) {
  ficha.id = id;
  ficha.renombrando = false;
  ficha.anadiendo = null;
  const dialogo = $("#ficha");
  if (!dialogo.open) dialogo.showModal();
  await recargarFicha();
}

async function recargarFicha() {
  try {
    const datos = await api(`/api/persona?id=${encodeURIComponent(ficha.id)}`);
    if (!datos.ok) throw new Error(datos.error?.mensaje || "No encontrada");
    ficha.datos = datos;
    ficha.id = datos.id;
    pintarFicha();
  } catch (e) {
    $("#ficha").close();
    avisar(e.message);
  }
}

async function tras_editar() {
  await recargar();
}

function pintarFicha() {
  const d = ficha.datos;
  const lleno = d.muestras.length >= d.max_muestras;
  const portada = el("div", { class: "cara__marco ficha__portada" },
    d.foto ? el("img", { src: d.foto, alt: d.id }) : el("div", { class: "iniciales", text: iniciales(d.id) }));

  let nombre;
  if (ficha.renombrando) {
    const entrada = el("input", { class: "ficha__entrada", value: d.id, maxlength: "100", "aria-label": "Nuevo nombre" });
    const guardar = async () => {
      const nuevo = entrada.value.trim();
      if (!nuevo || nuevo === d.id) { ficha.renombrando = false; pintarFicha(); return; }
      const r = await api(`/api/persona/renombrar?id=${encodeURIComponent(d.id)}&nuevo=${encodeURIComponent(nuevo)}`,
        { method: "POST", headers: { "X-JumeX-Lens": "1" } });
      if (!r.ok) { avisar(r.error?.mensaje || "No se pudo renombrar"); return; }
      ficha.renombrando = false;
      avisar(`Ahora se llama ${r.id}`);
      ficha.datos = r; ficha.id = r.id;
      pintarFicha();
      tras_editar();
    };
    entrada.addEventListener("keydown", (e) => { if (e.key === "Enter") guardar(); if (e.key === "Escape") { e.preventDefault(); ficha.renombrando = false; pintarFicha(); } });
    nombre = el("div", { class: "ficha__renombrar" }, entrada,
      el("button", { class: "boton boton--principal boton--pequeno", onclick: guardar, text: "Guardar" }),
      el("button", { class: "boton boton--secundario boton--pequeno", onclick: () => { ficha.renombrando = false; pintarFicha(); }, text: "Cancelar" }));
    requestAnimationFrame(() => { entrada.focus(); entrada.select(); });
  } else {
    nombre = el("h2", { id: "ficha-nombre", text: d.id });
  }

  const tiles = d.muestras.map((m, i) => el("figure", { class: `muestra${m.principal ? " muestra--principal" : ""}` },
    el("div", { class: "cara__marco" },
      m.foto ? el("img", { src: m.foto, alt: `Muestra ${i + 1} de ${d.id}`, loading: "lazy" })
             : el("div", { class: "muestra__sin-foto", text: "Sin foto (inscrita antes de que se guardaran)" })),
    m.principal ? el("span", { class: "muestra__insignia", text: "Principal" }) : null,
    el("figcaption", {},
      el("span", { text: `Muestra ${i + 1}` }),
      el("span", { class: "muestra__acciones" },
        m.foto && !m.principal ? el("button", { class: "muestra__boton", title: "Usar como foto principal", "aria-label": "Usar como foto principal", onclick: () => fijarPrincipal(m.clave) }, icono("estrella")) : null,
        el("button", { class: "muestra__boton muestra__boton--borrar", title: "Borrar esta muestra", "aria-label": `Borrar la muestra ${i + 1}`, onclick: () => borrarMuestra(m.clave) }, icono("papelera"))))));

  tiles.push(lleno
    ? el("div", { class: "muestra muestra--anadir muestra--lleno" }, el("p", { text: `Máximo de ${d.max_muestras} muestras` }))
    : tileAnadir());

  $("#ficha-contenido").replaceChildren(
    el("header", { class: "ficha__cabecera" },
      portada,
      el("div", { class: "ficha__datos" },
        el("p", { class: "antetitulo", text: "Ficha de la persona" }),
        nombre,
        el("p", { class: "texto-suave", text: `${d.muestras.length} muestra${d.muestras.length > 1 ? "s" : ""} de ${d.max_muestras} posibles · se compara con la más parecida` }),
        el("div", { class: "acciones" },
          ficha.renombrando ? null : el("button", { class: "boton boton--secundario boton--pequeno", onclick: () => { ficha.renombrando = true; pintarFicha(); }, text: "Cambiar nombre" }),
          el("button", { class: "boton boton--secundario boton--pequeno", text: "Verificar con una foto", onclick: () => {
            $("#ficha").close();
            $("#nombre").value = d.id;
            cambiarModo("verificar");
            window.scrollTo({ top: $("#vista-operacion").offsetTop - 16, behavior: reduceMovimiento ? "auto" : "smooth" });
            avisar(`Suelta una foto para comprobar si es ${d.id}`);
          } }))),
      el("button", { class: "ficha__cerrar", "aria-label": "Cerrar", onclick: () => $("#ficha").close() }, icono("no"))),
    el("h3", { class: "ficha__seccion", text: "Fotos guardadas (muestras)" }),
    el("p", { class: "texto-suave ficha__ayuda", text: "Cada muestra es un template distinto. Tener varias fotos (con luz, gafas o ángulos diferentes) ayuda a reconocerla mejor." }),
    el("div", { class: "muestras" }, tiles),
    el("footer", { class: "ficha__pie" },
      el("button", { class: "boton boton--peligro", onclick: () => eliminarPersona({ id: d.id, muestras: d.muestras.length }, true) }, icono("papelera"), "Eliminar persona"),
      el("button", { class: "boton boton--secundario", onclick: () => $("#ficha").close(), text: "Cerrar" })));
}

/** Casilla para añadir otra foto: soltar/elegir ► analizar ► confirmar ► inscribir. */
function tileAnadir() {
  const a = ficha.anadiendo;

  if (a?.estado === "analizando") {
    return el("div", { class: "muestra muestra--anadir" }, el("span", { class: "girando--azul" }), el("p", { text: "Buscando la cara…" }));
  }
  if (a?.estado === "revisar") {
    return el("div", { class: "muestra muestra--revisar" },
      el("div", { class: "cara__marco" }, el("img", { src: a.respuesta.retrato || a.respuesta.cara_alineada, alt: "Cara encontrada" })),
      el("p", { text: `¿Añadir esta cara a ${ficha.datos.id}?` }),
      el("div", { class: "muestra__confirmar" },
        el("button", { class: "boton boton--principal boton--pequeno", onclick: confirmarAnadir, text: "Añadir" }),
        el("button", { class: "boton boton--secundario boton--pequeno", onclick: () => abrirEnInscribir(a.archivo), text: "No es, la marco yo" })));
  }
  if (a?.estado === "error") {
    return el("div", { class: "muestra muestra--anadir muestra--error" },
      el("p", { text: a.mensaje }),
      el("button", { class: "boton boton--secundario boton--pequeno", onclick: () => abrirEnInscribir(a.archivo), text: "Marcar la cara a mano" }),
      el("button", { class: "boton boton--enlace", onclick: () => { ficha.anadiendo = null; pintarFicha(); }, text: "Probar otra" }));
  }
  const casilla = el("div", { class: "muestra muestra--anadir" },
    el("button", { class: "muestra__elegir", onclick: () => $("#selector-ficha").click() },
      icono("mas"), el("b", { text: "Añadir foto" }), el("small", { text: "Suelta una foto o haz clic" })),
    el("button", { class: "boton boton--enlace muestra__camara", onclick: () => abrirCamara(analizarParaFicha) },
      icono("camara"), "o hazte una foto"));
  ["dragenter", "dragover"].forEach((t) => casilla.addEventListener(t, (e) => { e.preventDefault(); casilla.classList.add("encima"); }));
  ["dragleave", "drop"].forEach((t) => casilla.addEventListener(t, () => casilla.classList.remove("encima")));
  casilla.addEventListener("drop", (e) => {
    e.preventDefault();
    const archivo = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
    if (archivo) analizarParaFicha(archivo);
  });
  return casilla;
}

async function analizarParaFicha(archivo) {
  ficha.anadiendo = { estado: "analizando", archivo };
  pintarFicha();
  let r;
  try {
    r = await api("/api/analizar", {
      method: "POST",
      headers: { "Content-Type": archivo.type || "application/octet-stream", "X-JumeX-Lens": "1" },
      body: archivo,
    });
  } catch (e) {
    r = { ok: false, error: { error: "red", mensaje: e.message } };
  }
  if (r.ok) ficha.anadiendo = { estado: "revisar", archivo, respuesta: r };
  else ficha.anadiendo = { estado: "error", archivo, mensaje: ERRORES[r.error?.error]?.[1] || r.error?.mensaje || "No se pudo analizar" };
  pintarFicha();
}

async function confirmarAnadir() {
  const { archivo, respuesta } = ficha.anadiendo;
  const elegida = (respuesta.detecciones || []).find((d) => d.elegida);
  const caja = elegida ? elegida.caja : respuesta.region;
  ficha.anadiendo = { estado: "analizando", archivo };
  pintarFicha();
  const parametros = new URLSearchParams({ id: ficha.datos.id });
  if (caja) parametros.set("caja", caja.join(","));
  const r = await api(`/api/inscribir?${parametros}`, {
    method: "POST",
    headers: { "Content-Type": archivo.type || "application/octet-stream", "X-JumeX-Lens": "1" },
    body: archivo,
  });
  ficha.anadiendo = null;
  if (r.ok) avisar(`Foto añadida: ${r.resultado.id} tiene ${r.resultado.muestras} muestras`);
  else avisar(ERRORES[r.error?.error]?.[1] || r.error?.mensaje || "No se pudo añadir");
  await recargarFicha();
  tras_editar();
}

/** Para casos difíciles: llevar la foto a la pestaña Inscribir, donde se puede marcar la cara. */
function abrirEnInscribir(archivo) {
  const id = ficha.datos.id;
  $("#ficha").close();
  $("#nombre").value = id;
  cambiarModo("inscribir");
  window.scrollTo({ top: $("#vista-operacion").offsetTop - 16, behavior: reduceMovimiento ? "auto" : "smooth" });
  recibirArchivo(archivo);
}

async function fijarPrincipal(clave) {
  const r = await api(`/api/persona/principal?id=${encodeURIComponent(ficha.datos.id)}&clave=${clave}`,
    { method: "POST", headers: { "X-JumeX-Lens": "1" } });
  if (!r.ok) { avisar(r.error?.mensaje || "No se pudo cambiar"); return; }
  ficha.datos = r;
  pintarFicha();
  tras_editar();
}

async function borrarMuestra(clave) {
  const d = ficha.datos;
  const ultima = d.muestras.length === 1;
  const pregunta = ultima
    ? `Es la única muestra de ${d.id}: si la borras, ${d.id} desaparece de la base de datos.\n\n¿Continuar?`
    : `¿Borrar esta muestra de ${d.id}?\n\nNo se puede deshacer.`;
  if (!confirm(pregunta)) return;
  const r = await api(`/api/muestras?id=${encodeURIComponent(d.id)}&clave=${clave}`,
    { method: "DELETE", headers: { "X-JumeX-Lens": "1" } });
  if (!r.ok) { avisar(r.error?.mensaje || "No se pudo borrar"); return; }
  if (r.resultado.quedan === 0) {
    $("#ficha").close();
    avisar(`${d.id} eliminado/a de la base de datos`);
  } else {
    avisar("Muestra borrada");
    await recargarFicha();
  }
  tras_editar();
}

async function eliminarPersona(p, desdeFicha = false) {
  const muestras = p.muestras > 1 ? ` y sus ${p.muestras} muestras` : "";
  if (!confirm(`¿Eliminar a ${p.id}${muestras} de la base de datos?\n\nNo se puede deshacer.`)) return;
  try {
    const r = await api(`/api/personas?id=${encodeURIComponent(p.id)}`, { method: "DELETE", headers: { "X-JumeX-Lens": "1" } });
    if (!r.ok) throw new Error(r.error?.mensaje || "error");
    if (estado.inscritoReciente === p.id) estado.inscritoReciente = null;
    if (desdeFicha) $("#ficha").close();
    avisar(`${p.id} eliminado/a de la base de datos`);
  } catch (e) {
    avisar(`No se pudo eliminar: ${e.message}`);
  }
  await recargar();
}

/* ------------------------------------------------------------------ */
/* Cámara: hacerse una foto en el momento                              */
/* ------------------------------------------------------------------ */
// getUserMedia solo existe en contextos seguros: localhost o https (el túnel lo es)
const camara = { stream: null, destino: null, frontal: true, camaras: 0 };

const ERRORES_CAMARA = {
  NotAllowedError: "Has denegado el permiso de la cámara. Actívalo en el candado de la barra de direcciones.",
  SecurityError: "El navegador no permite usar la cámara en esta página.",
  NotFoundError: "No se ha encontrado ninguna cámara en este equipo.",
  OverconstrainedError: "No se ha encontrado ninguna cámara en este equipo.",
  NotReadableError: "La cámara está ocupada por otra aplicación (¿Zoom, Teams, FaceTime?). Ciérrala y repite.",
  AbortError: "La cámara no ha respondido. Prueba otra vez.",
};

/** Abre la cámara; al confirmar la foto se llama a destino(File). */
async function abrirCamara(destino) {
  if (!navigator.mediaDevices?.getUserMedia) {
    avisar("La cámara solo funciona en localhost o con https, y en un navegador que la admita.");
    return;
  }
  camara.destino = destino;
  const dialogo = $("#camara");
  if (!dialogo.open) dialogo.showModal();
  await encenderCamara();
}

function estadoCamara(texto, error = false) {
  const nodo = $("#camara-estado");
  nodo.textContent = texto || "";
  nodo.classList.toggle("error", error);
}

function botonesCamara({ disparar = false, usar = false, repetir = false }) {
  $("#camara-disparar").hidden = usar;
  $("#camara-disparar").disabled = !disparar;
  $("#camara-usar").hidden = !usar;
  $("#camara-repetir").hidden = !repetir;
  $("#camara-cambiar").hidden = usar || camara.camaras < 2;
}

async function encenderCamara() {
  apagarCamara();
  const video = $("#camara-video");
  $("#camara-visor").classList.remove("congelada");
  $("#camara-foto").hidden = true;
  video.hidden = false;
  botonesCamara({});
  estadoCamara("Pidiendo permiso para usar la cámara…");
  try {
    camara.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: camara.frontal ? "user" : "environment", width: { ideal: 1280 }, height: { ideal: 960 } },
      audio: false,
    });
  } catch (e) {
    estadoCamara(ERRORES_CAMARA[e.name] || `No se pudo abrir la cámara (${e.name || e.message}).`, true);
    return;
  }
  if (!$("#camara").open) { apagarCamara(); return; } // se cerró mientras pedía permiso
  video.srcObject = camara.stream;
  // La frontal se ve como un espejo, igual que en el móvil
  const ajustes = camara.stream.getVideoTracks()[0]?.getSettings?.() || {};
  video.classList.toggle("espejo", ajustes.facingMode ? ajustes.facingMode === "user" : camara.frontal);
  try { await video.play(); } catch { /* autoplay: se reproduce igualmente al estar muted */ }
  try {
    camara.camaras = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput").length;
  } catch { camara.camaras = 1; }
  estadoCamara("");
  botonesCamara({ disparar: true });
  $("#camara-disparar").focus();
}

function apagarCamara() {
  camara.stream?.getTracks().forEach((t) => t.stop());
  camara.stream = null;
  const video = $("#camara-video");
  if (video) video.srcObject = null;
}

function dispararCamara() {
  const video = $("#camara-video");
  const ancho = video.videoWidth, alto = video.videoHeight;
  if (!camara.stream || !ancho) return;
  const lienzoFoto = $("#camara-foto");
  lienzoFoto.width = ancho;
  lienzoFoto.height = alto;
  const ctx = lienzoFoto.getContext("2d");
  // Se guarda tal y como se veía (en espejo con la frontal); a ArcFace le da igual:
  // también compara la cara volteada
  if (video.classList.contains("espejo")) { ctx.translate(ancho, 0); ctx.scale(-1, 1); }
  ctx.drawImage(video, 0, 0, ancho, alto);
  video.hidden = true;
  lienzoFoto.hidden = false;
  $("#camara-visor").classList.add("congelada");
  const flash = $("#camara-flash");
  flash.classList.remove("activo");
  void flash.offsetWidth;
  flash.classList.add("activo");
  estadoCamara("¿Sale bien? Úsala o repítela");
  botonesCamara({ usar: true, repetir: true });
  $("#camara-usar").focus();
}

function repetirCamara() {
  $("#camara-visor").classList.remove("congelada");
  $("#camara-foto").hidden = true;
  $("#camara-video").hidden = false;
  estadoCamara("");
  botonesCamara({ disparar: !!camara.stream });
}

function usarFotoCamara() {
  const destino = camara.destino;
  $("#camara-foto").toBlob((blob) => {
    if (!blob) { estadoCamara("No se pudo guardar la foto. Repite.", true); return; }
    const archivo = new File([blob], `camara-${Date.now()}.jpg`, { type: "image/jpeg" });
    $("#camara").close(); // el evento close apaga la cámara
    destino?.(archivo);
  }, "image/jpeg", 0.92);
}

function prepararCamara() {
  const dialogo = $("#camara");
  dialogo.addEventListener("close", () => { apagarCamara(); camara.destino = null; });
  dialogo.addEventListener("click", (e) => { if (e.target === dialogo) dialogo.close(); }); // clic fuera
  $("#camara-cerrar").addEventListener("click", () => dialogo.close());
  $("#camara-disparar").addEventListener("click", dispararCamara);
  $("#camara-usar").addEventListener("click", usarFotoCamara);
  $("#camara-repetir").addEventListener("click", repetirCamara);
  $("#camara-cambiar").addEventListener("click", () => { camara.frontal = !camara.frontal; encenderCamara(); });
  // Espacio o Intro con el visor en marcha: hacer la foto
  dialogo.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && e.target === dialogo && !$("#camara-disparar").disabled
        && !$("#camara-disparar").hidden) { e.preventDefault(); dispararCamara(); }
  });
}

/* ------------------------------------------------------------------ */
/* Avisos                                                              */
/* ------------------------------------------------------------------ */
let temporizadorAviso = 0;
function avisar(texto) {
  const aviso = $("#aviso");
  // Un <dialog> modal tapa todo lo demás: con uno abierto, el aviso va dentro del de más arriba
  const destino = ["#camara", "#ficha"].map((s) => $(s)).find((d) => d.open) || document.body;
  if (aviso.parentElement !== destino) destino.append(aviso);
  aviso.textContent = texto;
  aviso.hidden = false;
  clearTimeout(temporizadorAviso);
  temporizadorAviso = setTimeout(() => { aviso.hidden = true; }, 3200);
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */
function iniciar() {
  $$(".pestana").forEach((b) => b.addEventListener("click", () => {
    if (estado.ocupado) { avisar("Espera a que termine el análisis"); return; }
    cambiarModo(b.dataset.modo, { conservarFoto: b.dataset.modo !== "bd" });
  }));
  prepararZona();
  $("#nombre").addEventListener("input", () => {
    actualizarAyudaNombre();
    if (estado.archivo && !estado.ocupado) {
      mostrarBotones({ ejecutar: true, otra: true });
      $("#escenario-estado").textContent = "";
    }
  });
  $("#nombre").addEventListener("keydown", (e) => { if (e.key === "Enter") ejecutar(); });
  $("#boton-ejecutar").addEventListener("click", (e) => { e.stopPropagation(); ejecutar(); });
  $("#boton-otra").addEventListener("click", (e) => { e.stopPropagation(); reiniciarTodo(); $("#selector").click(); });
  $("#boton-otra-camara").addEventListener("click", (e) => {
    e.stopPropagation();
    if (estado.ocupado) return;
    reiniciarTodo();
    abrirCamara(recibirArchivo);
  });
  prepararCamara();
  // Login de administrador (pastilla de la cabecera)
  $("#rol").addEventListener("click", abrirLogin);
  $("#login-form").addEventListener("submit", entrarAdmin);
  $("#login-cerrar").addEventListener("click", () => $("#login").close());
  $("#login-cancelar").addEventListener("click", () => $("#login").close());
  $("#login").addEventListener("click", (e) => { if (e.target === $("#login")) $("#login").close(); });
  $("#boton-manual").addEventListener("click", (e) => { e.stopPropagation(); entrarSeleccion(); });
  $("#boton-usar").addEventListener("click", (e) => { e.stopPropagation(); usarSeleccion(); });
  $("#boton-cancelar").addEventListener("click", (e) => { e.stopPropagation(); salirSeleccion(); });
  prepararSeleccion();
  $("#buscador").addEventListener("input", pintarBD);
  const selectorFicha = $("#selector-ficha");
  selectorFicha.addEventListener("change", () => {
    if (selectorFicha.files[0]) analizarParaFicha(selectorFicha.files[0]);
    selectorFicha.value = "";
  });
  const dialogo = $("#ficha");
  dialogo.addEventListener("click", (e) => { if (e.target === dialogo) dialogo.close(); });  // clic fuera

  let temporizadorResize = 0;
  window.addEventListener("resize", () => {
    clearTimeout(temporizadorResize);
    temporizadorResize = setTimeout(() => {
      if (!estado.bitmap || $("#escenario").hidden) return;
      ajustarLienzo();
      if (estado.seleccion) { estado.seleccion.rect = null; dibujarSeleccion(); mostrarBotones({ cancelar: true }); }
      else if (estado.ultimaRespuesta && !estado.ocupado) { lienzo.raf = 0; animarDetecciones(estado.ultimaRespuesta); }
      else if (!estado.ocupado) pintarFoto(0.15);
    }, 120);
  });

  cambiarModo("verificar");
  recargar();
}

document.addEventListener("DOMContentLoaded", iniciar);
