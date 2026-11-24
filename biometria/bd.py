"""Base de datos de templates en CSV con integridad (HMAC) y cifrado opcional.

Formato del fichero (una fila por muestra; una persona puede tener varias):

    id,template,hmac
    Afton Smith,f32:AACAPwAAgD4...,9f3a1c...
    Afton Smith,f32:AAAgPwAAQD8...,b72e04...

- template: "f32:" + base64 del vector float32 little-endian, o, con
  `bd.cifrar_templates: true`, "gcm:" + base64(nonce || AES-256-GCM(vector)).
  El prefijo hace que el fichero se pueda leer aunque se cambie la opción.
- hmac: HMAC-SHA256 de (posición de la fila, número total de filas, id,
  template). Al incluir posición y total, borrar, duplicar o reordenar filas
  también rompe la integridad, no solo modificarlas.

Cada operación vuelve a leer y verificar el fichero entero: si alguien lo
modifica entre dos llamadas, la siguiente lo detecta. La escritura es atómica
(fichero temporal + os.replace), así un fallo a mitad no deja un CSV a medias.

Clave: variable de entorno BIOMETRIA_HMAC_KEY o, si no existe, el fichero
`bd.ruta_clave` (se crea con 64 caracteres aleatorios al inscribir por primera
vez). De ella se derivan con HKDF dos claves independientes: una para el HMAC y
otra para el cifrado.

Rollback (con `bd.anti_rollback: true`): tras cada escritura se guarda un
**sello** (SHA-256 del CSV, firmado con HMAC) en la carpeta de la CLAVE, no en la
de la BD. Al leer, el CSV debe coincidir con el sello: volver a una copia antigua
completa y válida, o borrar el fichero, se detecta. Sin sello previo (primera
vez) se confía en el CSV actual y se sella.

Concurrencia: las escrituras (leer, modificar, reescribir) se hacen con un
bloqueo de fichero (`flock`), así dos procesos que inscriben a la vez no se
pisan y ninguna muestra se pierde. En Windows no hay `fcntl`: el bloqueo no se
aplica (la escritura sigue siendo atómica).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import struct
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from biometria.config import Config
from biometria.errores import (
    BDCorrupta,
    ConfiguracionInvalida,
    IdInvalido,
    IdNoInscrito,
    IntegridadBD,
    LimiteMuestras,
    MuestraNoEncontrada,
    TemplateInvalido,
)
from biometria.extractores import Extractor

try:  # bloqueo entre procesos (macOS/Linux)
    import fcntl
except ImportError:  # Windows
    fcntl = None

CABECERA = ["id", "template", "hmac"]
VERSION = b"jumex-bd-v1"
VARIABLE_CLAVE = "BIOMETRIA_HMAC_KEY"
LONGITUD_MINIMA_CLAVE = 32
PREFIJO_PLANO = "f32:"
PREFIJO_CIFRADO = "gcm:"
TAM_NONCE = 12

# Identificador: empieza por letra o número (así nunca empieza por = + - @,
# que Excel interpretaría como fórmula al abrir el CSV) y sigue con letras,
# números, espacios, punto, guion, guion bajo o apóstrofo. Admite acentos y ñ.
LONGITUD_MAXIMA_ID = 100
PATRON_ID = re.compile(rf"[^\W_][\w .'\-]{{0,{LONGITUD_MAXIMA_ID - 1}}}")


def validar_id(id_persona) -> str:
    """Normaliza (Unicode NFC, sin espacios a los lados) y valida un identificador.

    NFC hace que "José" escrito con la tilde como carácter aparte o pegada a
    la e sea el mismo id.
    """
    if not isinstance(id_persona, str):
        raise IdInvalido("El id debe ser texto")
    normalizado = unicodedata.normalize("NFC", id_persona).strip()
    if not normalizado:
        raise IdInvalido("El id no puede estar vacío")
    if len(normalizado) > LONGITUD_MAXIMA_ID:
        raise IdInvalido(f"El id no puede tener más de {LONGITUD_MAXIMA_ID} caracteres")
    if not PATRON_ID.fullmatch(normalizado):
        raise IdInvalido(
            "El id debe empezar por letra o número y solo puede contener letras, "
            "números, espacios y los signos . - _ '"
        )
    return normalizado


def clave_busqueda(id_persona) -> str:
    """Forma de un id para BUSCARLO: sin mayúsculas, sin tildes ni otros diacríticos y
    con los espacios simplificados. "josé  pérez" y "Jose Perez" dan la misma clave.

    El id guardado no cambia (ni su HMAC): esto solo decide a qué persona inscrita se
    refiere un id escrito de otra forma. Así un "lionel messi" o un "Nicolas Maduro"
    sin tilde no dan un falso rechazo que no es culpa del reconocimiento."""
    base = unicodedata.normalize("NFD", validar_id(id_persona))
    sin_marcas = "".join(c for c in base if unicodedata.category(c) != "Mn")
    return " ".join(sin_marcas.casefold().split())


@dataclass(frozen=True)
class Muestra:
    id: str
    template: np.ndarray  # float32, validado por el extractor


def _derivar(clave: bytes, uso: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=VERSION + b"/" + uso).derive(clave)


def _mensaje(*partes: bytes) -> bytes:
    """Concatena con la longitud delante de cada parte: sin ambigüedad posible
    entre ("ab", "c") y ("a", "bc")."""
    return b"".join(struct.pack(">Q", len(p)) + p for p in partes)


def _escribir_atomico(ruta: Path, datos: bytes) -> None:
    """Fichero temporal + fsync + os.replace: nunca queda un fichero a medias."""
    descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as f:
            f.write(datos)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise


class BaseDatos:
    """Acceso a bd.csv. Cada método lee y verifica el fichero de nuevo."""

    def __init__(self, cfg: Config, extractor: Extractor):
        self.cfg = cfg
        self.extractor = extractor
        self.ruta = cfg.bd.ruta
        self._claves: tuple[bytes, bytes, bytes, bytes] | None = None

    # ------------------------------------------------------------------ #
    # Claves
    # ------------------------------------------------------------------ #
    def _clave_maestra(self, crear: bool) -> bytes:
        texto = os.environ.get(VARIABLE_CLAVE)
        origen = f"la variable de entorno {VARIABLE_CLAVE}"
        if texto is None:
            ruta = self.cfg.bd.ruta_clave
            origen = f"el fichero {ruta}"
            if ruta.is_file():
                texto = ruta.read_text(encoding="utf-8").strip()
            elif crear:
                texto = secrets.token_hex(32)
                ruta.parent.mkdir(parents=True, exist_ok=True)
                # O_EXCL: si otro proceso la acaba de crear, no se pisa. 0o600: solo el dueño.
                descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as f:
                    f.write(texto + "\n")
            else:
                raise ConfiguracionInvalida(
                    f"Falta la clave de la base de datos: define {VARIABLE_CLAVE} o crea {ruta}"
                )
        if len(texto) < LONGITUD_MINIMA_CLAVE:
            raise ConfiguracionInvalida(
                f"La clave de {origen} es demasiado corta (mínimo {LONGITUD_MINIMA_CLAVE} caracteres)"
            )
        return texto.encode("utf-8")

    def _obtener_claves(self, crear: bool = False) -> tuple[bytes, bytes, bytes, bytes]:
        """(HMAC de la BD, AES, firma de templates, sello anti-rollback), derivadas de la maestra."""
        if self._claves is None:
            maestra = self._clave_maestra(crear)
            self._claves = (_derivar(maestra, b"hmac"), _derivar(maestra, b"aes-gcm"),
                            _derivar(maestra, b"template-sig"), _derivar(maestra, b"sello"))
        return self._claves

    def clave_junto_a_bd(self) -> bool:
        """True si la clave se lee de un fichero en la misma carpeta que la BD (o dentro).

        Es la configuración débil: quien puede escribir la BD suele poder leer
        también la clave, y entonces puede volver a firmar lo que quiera."""
        if os.environ.get(VARIABLE_CLAVE) is not None:
            return False
        carpeta_bd = self.ruta.resolve().parent
        return self.cfg.bd.ruta_clave.resolve().is_relative_to(carpeta_bd)

    # ------------------------------------------------------------------ #
    # Sello anti-rollback
    # ------------------------------------------------------------------ #
    def ruta_sello(self) -> Path:
        """Junto a la clave (no junto a la BD), con un nombre propio de cada BD."""
        nombre = hashlib.sha256(str(self.ruta.resolve()).encode("utf-8")).hexdigest()[:16]
        return self.cfg.bd.ruta_clave.parent / f"sello-{nombre}.json"

    def _firma_sello(self, resumen: str, pendiente: str) -> str:
        mensaje = _mensaje(VERSION, b"sello", str(self.ruta.resolve()).encode("utf-8"),
                           resumen.encode("ascii"), pendiente.encode("ascii"))
        return hmac.new(self._obtener_claves()[3], mensaje, hashlib.sha256).hexdigest()

    @staticmethod
    def _resumen(contenido: bytes | None) -> str:
        return hashlib.sha256(contenido).hexdigest() if contenido is not None else "vacia"

    def _escribir_sello(self, actual: str, pendiente: str = "") -> None:
        """Guarda el resumen del CSV vigente y, durante una escritura, el del que viene.

        Dos fases: antes de reemplazar el CSV se anuncia `pendiente`; después se
        confirma como `actual`. Un lector que llegue en medio acepta cualquiera de
        los dos, y si el proceso muere a mitad tampoco queda un falso rollback."""
        datos = json.dumps({"bd": str(self.ruta.resolve()), "sha256": actual, "pendiente": pendiente,
                            "hmac": self._firma_sello(actual, pendiente)}).encode("utf-8")
        ruta = self.ruta_sello()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(ruta, datos)

    def _leer_sello(self) -> tuple[str, str] | None:
        """(actual, pendiente) del sello verificado, o None si aún no hay sello."""
        ruta = self.ruta_sello()
        if not ruta.is_file():
            return None
        try:
            sello = json.loads(ruta.read_text(encoding="utf-8"))
            resumen, pendiente, firma = str(sello["sha256"]), str(sello.get("pendiente", "")), str(sello["hmac"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise IntegridadBD(f"El sello anti-rollback ({ruta}) está dañado") from e
        if (not (firma.isascii() and resumen.isascii() and pendiente.isascii())
                or not hmac.compare_digest(firma, self._firma_sello(resumen, pendiente))):
            raise IntegridadBD(f"El sello anti-rollback ({ruta}) no es válido: alguien lo ha modificado")
        return resumen, pendiente

    def _comprobar_sello(self, contenido: bytes | None) -> None:
        """Compara el CSV actual con el sello.

        Sin sello previo y con una BD que ya existe: con `bd.sello_estricto`, error
        (alguien puede haber borrado el sello para colar una copia antigua); sin él,
        se sella el CSV actual (TOFU). Para aceptar a propósito la BD actual: sellar()."""
        if not self.cfg.bd.anti_rollback:
            return
        sello = self._leer_sello()
        if sello is None:
            if contenido is not None:
                if self.cfg.bd.sello_estricto:
                    raise IntegridadBD(
                        f"La base de datos existe pero falta su sello anti-rollback ({self.ruta_sello()}). "
                        "Alguien puede haberlo borrado para colar una copia antigua. Si la BD es la "
                        "buena (primera vez desde este equipo o con esta ruta, o tras restaurar un "
                        "respaldo a propósito), séllala con: python -m biometria sellar")
                self._escribir_sello(self._resumen(contenido))
            return
        actual = self._resumen(contenido)
        if not any(r and hmac.compare_digest(actual, r) for r in sello):
            ruta = self.ruta_sello()
            if contenido is None:
                raise IntegridadBD("La base de datos ha desaparecido: el sello dice que existía. "
                                   f"Si la has borrado tú a propósito, borra también {ruta}")
            raise IntegridadBD(
                "La base de datos no es la última que escribió el sistema (rollback): alguien ha "
                "vuelto a una copia anterior o la ha cambiado entera")

    # ------------------------------------------------------------------ #
    # Bloqueo entre procesos
    # ------------------------------------------------------------------ #
    @contextlib.contextmanager
    def _bloqueo(self):
        """Exclusión mutua para leer-modificar-escribir el CSV (también entre procesos)."""
        if fcntl is None:
            yield
            return
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ruta.parent / f".{self.ruta.name}.lock", "a+b") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _hmac(self, indice: int, total: int, id_persona: str, campo: str) -> str:
        mensaje = _mensaje(VERSION, str(indice).encode(), str(total).encode(),
                           id_persona.encode("utf-8"), campo.encode("ascii"))
        return hmac.new(self._obtener_claves()[0], mensaje, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------ #
    # Firma de templates (defensa contra templates fabricados en _t)
    # ------------------------------------------------------------------ #
    def firmar_template(self, template: np.ndarray) -> str:
        """HMAC-SHA256 (hex) del template que ESTE sistema ha generado.

        Sirve para que los métodos _t puedan distinguir un template nuestro de uno
        fabricado a mano por un atacante. La clave sale de la misma clave maestra,
        así que la firma solo la puede producir quien tiene la clave. Ver THREAT_MODEL.md.
        """
        datos = np.asarray(template, dtype="<f4").tobytes()
        mensaje = _mensaje(VERSION, b"template", datos)
        # crear=True: si es la primera operación del sistema, se crea la clave como al inscribir
        return hmac.new(self._obtener_claves(crear=True)[2], mensaje, hashlib.sha256).hexdigest()

    def template_firmado(self, template: np.ndarray, firma: str | None) -> bool:
        """True si `firma` es una firma válida de `template` hecha por este sistema."""
        if not firma or not isinstance(firma, str) or not firma.isascii():
            return False
        try:
            esperada = self.firmar_template(template)
        except ConfiguracionInvalida:
            return False
        return hmac.compare_digest(firma, esperada)

    # ------------------------------------------------------------------ #
    # Serialización de templates
    # ------------------------------------------------------------------ #
    def _codificar(self, id_persona: str, template: np.ndarray) -> str:
        datos = template.astype("<f4").tobytes()
        if not self.cfg.bd.cifrar_templates:
            return PREFIJO_PLANO + base64.b64encode(datos).decode("ascii")
        nonce = secrets.token_bytes(TAM_NONCE)
        # El id va como dato asociado: un template cifrado no se puede mover a otra persona.
        cifrado = AESGCM(self._obtener_claves()[1]).encrypt(nonce, datos, id_persona.encode("utf-8"))
        return PREFIJO_CIFRADO + base64.b64encode(nonce + cifrado).decode("ascii")

    def _decodificar(self, numero_fila: int, id_persona: str, campo: str) -> np.ndarray:
        try:
            if campo.startswith(PREFIJO_PLANO):
                datos = base64.b64decode(campo[len(PREFIJO_PLANO):], validate=True)
            elif campo.startswith(PREFIJO_CIFRADO):
                bruto = base64.b64decode(campo[len(PREFIJO_CIFRADO):], validate=True)
                datos = AESGCM(self._obtener_claves()[1]).decrypt(
                    bruto[:TAM_NONCE], bruto[TAM_NONCE:], id_persona.encode("utf-8"))
            else:
                raise BDCorrupta(f"Fila {numero_fila}: formato de template desconocido")
        except (binascii.Error, ValueError, InvalidTag) as e:
            raise BDCorrupta(f"Fila {numero_fila}: template ilegible ({type(e).__name__})") from e
        if len(datos) % 4:
            raise BDCorrupta(f"Fila {numero_fila}: el template no es un vector float32")
        try:
            return self.extractor.validar_template(np.frombuffer(datos, dtype="<f4"))
        except TemplateInvalido as e:
            raise BDCorrupta(
                f"Fila {numero_fila}: {e.mensaje}. ¿La BD se creó con otro extractor o parámetros?"
            ) from e

    # ------------------------------------------------------------------ #
    # Lectura y escritura del fichero
    # ------------------------------------------------------------------ #
    def _leer_filas(self, comprobar_sello: bool = True) -> list[tuple[str, str]]:
        """Lee y verifica el CSV. Devuelve (id, campo template) por fila."""
        if not self.ruta.is_file():
            if comprobar_sello:
                self._comprobar_sello(None)  # ¿la han borrado? (solo con anti_rollback)
            return []
        bruto = self.ruta.read_bytes()
        try:
            texto = bruto.decode("utf-8")
        except UnicodeDecodeError as e:
            raise BDCorrupta("La base de datos no es texto UTF-8") from e
        try:
            filas = list(csv.reader(io.StringIO(texto, newline="")))
        except csv.Error as e:
            raise BDCorrupta(f"CSV mal formado: {e}") from e
        if not filas or filas[0] != CABECERA:
            raise BDCorrupta(f"La cabecera de la base de datos debe ser {','.join(CABECERA)}")

        datos = filas[1:]
        total = len(datos)
        resultado = []
        for indice, fila in enumerate(datos):
            numero = indice + 2  # número de línea en el fichero (la 1 es la cabecera)
            if len(fila) != 3:
                raise BDCorrupta(f"Fila {numero}: se esperaban 3 columnas y hay {len(fila)}")
            id_persona, campo, firma = fila
            if not campo.isascii():
                raise BDCorrupta(f"Fila {numero}: el template contiene caracteres no válidos")
            # La firma se comprueba ANTES de interpretar nada más de la fila.
            # (compare_digest exige ASCII: una firma con otros caracteres ya es inválida.)
            if not firma.isascii() or not hmac.compare_digest(
                    firma, self._hmac(indice, total, id_persona, campo)):
                raise IntegridadBD(
                    f"Fila {numero}: el HMAC no coincide. La base de datos se ha modificado "
                    "fuera del sistema (filas cambiadas, borradas, duplicadas o reordenadas) "
                    "o la clave no es la correcta"
                )
            resultado.append((id_persona, campo))
        # Filas válidas una a una; ahora, ¿es el CSV que escribimos la última vez?
        if comprobar_sello:
            self._comprobar_sello(bruto)
        return resultado

    def sellar(self) -> int:
        """Acepta A PROPÓSITO la BD actual como la buena y la sella (anti-rollback).

        Verifica antes el HMAC de todas las filas (sin clave no se puede sellar nada
        falso). Para cuando falta el sello con `bd.sello_estricto`: primera vez desde
        otro equipo o ruta (p. ej. Docker y local), o tras restaurar un respaldo.
        Devuelve el número de filas."""
        with self._bloqueo():
            filas = self._leer_filas(comprobar_sello=False)
            self._obtener_claves()
            contenido = self.ruta.read_bytes() if self.ruta.is_file() else None
            if contenido is None:
                raise ConfiguracionInvalida(f"No hay base de datos en {self.ruta}: nada que sellar")
            self._escribir_sello(self._resumen(contenido))
            return len(filas)

    def _escribir_filas(self, filas: list[tuple[str, str]]) -> None:
        total = len(filas)
        salida = io.StringIO(newline="")
        escritor = csv.writer(salida, lineterminator="\n")
        escritor.writerow(CABECERA)
        for indice, (id_persona, campo) in enumerate(filas):
            escritor.writerow([id_persona, campo, self._hmac(indice, total, id_persona, campo)])

        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = salida.getvalue().encode("utf-8")
        if not self.cfg.bd.anti_rollback:
            _escribir_atomico(self.ruta, contenido)
            return
        # Sello en dos fases (ver _escribir_sello). Quien escribe ya ha leído y
        # verificado el CSV, así que el resumen vigente es el del fichero actual.
        anterior = self._resumen(self.ruta.read_bytes() if self.ruta.is_file() else None)
        nuevo = self._resumen(contenido)
        self._escribir_sello(anterior, pendiente=nuevo)
        _escribir_atomico(self.ruta, contenido)
        self._escribir_sello(nuevo)

    def _cargar(self) -> list[tuple[str, str, np.ndarray]]:
        """Lee, verifica la integridad y decodifica: (id, campo template, template) por fila."""
        resultado = []
        for numero, (id_persona, campo) in enumerate(self._leer_filas(), start=2):
            try:
                id_valido = validar_id(id_persona)
            except IdInvalido as e:
                raise BDCorrupta(f"Fila {numero}: {e.mensaje}") from e
            if id_valido != id_persona:
                raise BDCorrupta(f"Fila {numero}: el id no está normalizado")
            resultado.append((id_persona, campo, self._decodificar(numero, id_persona, campo)))
        return resultado

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    def muestras(self) -> list[Muestra]:
        """Todas las muestras, verificadas, en el orden del fichero."""
        return [Muestra(id_persona, template) for id_persona, _, template in self._cargar()]

    def por_id(self) -> dict[str, list[np.ndarray]]:
        """{id: [templates]} con todas las muestras de cada persona."""
        agrupadas: dict[str, list[np.ndarray]] = defaultdict(list)
        for muestra in self.muestras():
            agrupadas[muestra.id].append(muestra.template)
        return dict(agrupadas)

    def ids(self) -> list[str]:
        return sorted(self.por_id())

    def resolver_id(self, id_persona: str, inscritos=None) -> str | None:
        """El id INSCRITO al que se refiere `id_persona`: el mismo, o el que solo se
        diferencia en mayúsculas, tildes o espacios (ver clave_busqueda). None si no
        hay nadie. `inscritos` (ids ya leídos) evita volver a leer el fichero.

        Si dos inscritos dan la misma clave (p. ej. "Jose" y "José"), solo vale el id
        exacto: con otra forma, IdInvalido, para no elegir a uno a ciegas."""
        id_persona = validar_id(id_persona)
        if inscritos is None:
            inscritos = {id_fila for id_fila, _ in self._leer_filas()}
        if id_persona in inscritos:
            return id_persona
        clave = clave_busqueda(id_persona)
        coinciden = sorted(i for i in inscritos if clave_busqueda(i) == clave)
        if len(coinciden) > 1:
            raise IdInvalido(f"'{id_persona}' puede referirse a {', '.join(coinciden)}: escríbelo exactamente")
        return coinciden[0] if coinciden else None

    def id_canonico(self, id_persona: str) -> str:
        """El id inscrito al que se refiere `id_persona`, o el propio id (validado) si es nuevo."""
        return self.resolver_id(id_persona) or validar_id(id_persona)

    def templates_de(self, id_persona: str) -> list[np.ndarray]:
        agrupadas = self.por_id()
        inscrito = self.resolver_id(id_persona, agrupadas)
        if inscrito is None:
            raise IdNoInscrito(f"'{validar_id(id_persona)}' no está inscrito")
        return agrupadas[inscrito]

    def inscribir(self, id_persona: str, template) -> int:
        """Añade una muestra. Devuelve cuántas muestras tiene ya esa persona."""
        id_persona = validar_id(id_persona)
        template = self.extractor.validar_template(template)
        with self._bloqueo():
            self._obtener_claves(crear=not self.ruta.is_file())
            # Se verifica y decodifica el fichero completo antes de reescribirlo:
            # nunca se vuelve a firmar contenido que no pasó la verificación.
            filas = self._cargar()
            # "lionel messi" añade una muestra a "Lionel Messi", no crea a otra persona
            id_persona = self.resolver_id(id_persona, {i for i, _, _ in filas}) or id_persona
            existentes = sum(id_fila == id_persona for id_fila, _, _ in filas)
            if existentes >= self.cfg.limites.max_muestras_por_id:
                raise LimiteMuestras(
                    f"'{id_persona}' ya tiene {existentes} muestras "
                    f"(máximo {self.cfg.limites.max_muestras_por_id})"
                )
            nuevas = [(id_fila, campo) for id_fila, campo, _ in filas]
            nuevas.append((id_persona, self._codificar(id_persona, template)))
            self._escribir_filas(nuevas)
            return existentes + 1

    def eliminar(self, id_persona: str) -> int:
        """Borra todas las muestras de una persona. Devuelve cuántas se han borrado."""
        id_persona = validar_id(id_persona)
        with self._bloqueo():
            filas = self._cargar()  # verifica la integridad antes de reescribir
            id_persona = self.resolver_id(id_persona, {i for i, _, _ in filas}) or id_persona
            quedan = [(id_fila, campo) for id_fila, campo, _ in filas if id_fila != id_persona]
            borradas = len(filas) - len(quedan)
            if borradas == 0:
                raise IdNoInscrito(f"'{id_persona}' no está inscrito")
            self._escribir_filas(quedan)
            return borradas

    def recodificar(self) -> dict[str, dict[str, str]]:
        """Reescribe todas las muestras con la codificación actual (p. ej. para CIFRAR
        una BD que estaba en claro tras poner `cifrar_templates: true`).

        Devuelve {id: {clave antigua: clave nueva}} para mover las fotos de la galería
        (la clave de una muestra es el hash de su campo, que cambia al cifrar)."""
        with self._bloqueo():
            filas = self._cargar()
            cambios: dict[str, dict[str, str]] = defaultdict(dict)
            nuevas = []
            for id_fila, campo, template in filas:
                cifrado = campo.startswith(PREFIJO_CIFRADO)
                nuevo = campo if cifrado == self.cfg.bd.cifrar_templates else self._codificar(id_fila, template)
                cambios[id_fila][self.clave_muestra(campo)] = self.clave_muestra(nuevo)
                nuevas.append((id_fila, nuevo))
            if filas:
                self._escribir_filas(nuevas)
            return dict(cambios)

    # ------------------------------------------------------------------ #
    # Muestras individuales (para editar la BD desde la web)
    # ------------------------------------------------------------------ #
    @staticmethod
    def clave_muestra(campo: str) -> str:
        """Identificador estable de una muestra: hash de su campo template.

        No hace falta otra columna en el CSV. Dos muestras con la misma clave
        tendrían el template idéntico (son la misma muestra repetida).
        """
        return hashlib.sha256(campo.encode("ascii")).hexdigest()[:16]

    def muestras_de(self, id_persona: str) -> list[tuple[str, np.ndarray]]:
        """[(clave, template)] de una persona, en el orden del fichero."""
        filas = self._cargar()
        id_persona = self.resolver_id(id_persona, {i for i, _, _ in filas}) or validar_id(id_persona)
        muestras = [(self.clave_muestra(campo), template)
                    for id_fila, campo, template in filas if id_fila == id_persona]
        if not muestras:
            raise IdNoInscrito(f"'{id_persona}' no está inscrito")
        return muestras

    def eliminar_muestra(self, id_persona: str, clave: str) -> int:
        """Borra una muestra concreta. Devuelve cuántas le quedan a esa persona."""
        id_persona = validar_id(id_persona)
        with self._bloqueo():
            filas = self._cargar()
            id_persona = self.resolver_id(id_persona, {i for i, _, _ in filas}) or id_persona
            quedan = [(i, c) for i, c, _ in filas
                      if not (i == id_persona and self.clave_muestra(c) == clave)]
            if len(quedan) == len(filas):
                raise MuestraNoEncontrada(f"'{id_persona}' no tiene ninguna muestra con clave {clave}")
            self._escribir_filas(quedan)
            return sum(i == id_persona for i, _ in quedan)

    def renombrar(self, id_persona: str, nuevo: str) -> dict[str, str]:
        """Cambia el id de todas las muestras de una persona.

        Devuelve {clave antigua: clave nueva}: las claves solo cambian con los
        templates cifrados, que llevan el id como dato asociado y hay que volver
        a cifrar.
        """
        id_persona, nuevo = validar_id(id_persona), validar_id(nuevo)
        with self._bloqueo():
            filas = self._cargar()
            inscritos = {i for i, _, _ in filas}
            id_persona = self.resolver_id(id_persona, inscritos) or id_persona
            if id_persona not in inscritos:
                raise IdNoInscrito(f"'{id_persona}' no está inscrito")
            # Otro inscrito con el mismo nombre (salvo mayúsculas/tildes) también choca;
            # cambiar solo las mayúsculas o tildes de uno mismo sí se puede
            if nuevo != id_persona and self.resolver_id(nuevo, inscritos - {id_persona}) is not None:
                raise IdInvalido(f"Ya hay alguien inscrito como '{nuevo}'")
            claves: dict[str, str] = {}
            nuevas = []
            for i, campo, template in filas:
                if i == id_persona:
                    nuevo_campo = (self._codificar(nuevo, template) if campo.startswith(PREFIJO_CIFRADO)
                                   else campo)
                    claves[self.clave_muestra(campo)] = self.clave_muestra(nuevo_campo)
                    nuevas.append((nuevo, nuevo_campo))
                else:
                    nuevas.append((i, campo))
            self._escribir_filas(nuevas)
            return claves

    def claves_por_id(self) -> dict[str, list[str]]:
        """{id: [claves de sus muestras]} en el orden del fichero."""
        claves: dict[str, list[str]] = defaultdict(list)
        for id_fila, campo, _ in self._cargar():
            claves[id_fila].append(self.clave_muestra(campo))
        return dict(claves)
