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

Lo que NO detecta (va en THREAT_MODEL.md): volver a una copia antigua completa
y válida del CSV (rollback) y borrar el fichero entero (equivale a una BD vacía:
denegación de servicio, pero no permite suplantar a nadie).
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import hmac
import io
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


class BaseDatos:
    """Acceso a bd.csv. Cada método lee y verifica el fichero de nuevo."""

    def __init__(self, cfg: Config, extractor: Extractor):
        self.cfg = cfg
        self.extractor = extractor
        self.ruta = cfg.bd.ruta
        self._claves: tuple[bytes, bytes] | None = None

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

    def _obtener_claves(self, crear: bool = False) -> tuple[bytes, bytes]:
        """(clave HMAC, clave AES) derivadas de la clave maestra."""
        if self._claves is None:
            maestra = self._clave_maestra(crear)
            self._claves = (_derivar(maestra, b"hmac"), _derivar(maestra, b"aes-gcm"))
        return self._claves

    def _hmac(self, indice: int, total: int, id_persona: str, campo: str) -> str:
        mensaje = _mensaje(VERSION, str(indice).encode(), str(total).encode(),
                           id_persona.encode("utf-8"), campo.encode("ascii"))
        return hmac.new(self._obtener_claves()[0], mensaje, hashlib.sha256).hexdigest()

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
    def _leer_filas(self) -> list[tuple[str, str]]:
        """Lee y verifica el CSV. Devuelve (id, campo template) por fila."""
        if not self.ruta.is_file():
            return []
        try:
            texto = self.ruta.read_text(encoding="utf-8")
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
        return resultado

    def _escribir_filas(self, filas: list[tuple[str, str]]) -> None:
        total = len(filas)
        salida = io.StringIO(newline="")
        escritor = csv.writer(salida, lineterminator="\n")
        escritor.writerow(CABECERA)
        for indice, (id_persona, campo) in enumerate(filas):
            escritor.writerow([id_persona, campo, self._hmac(indice, total, id_persona, campo)])

        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporal = tempfile.mkstemp(dir=self.ruta.parent, prefix=".bd-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as f:
                f.write(salida.getvalue())
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporal, self.ruta)
        except BaseException:
            Path(temporal).unlink(missing_ok=True)
            raise

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

    def templates_de(self, id_persona: str) -> list[np.ndarray]:
        id_persona = validar_id(id_persona)
        templates = self.por_id().get(id_persona)
        if not templates:
            raise IdNoInscrito(f"'{id_persona}' no está inscrito")
        return templates

    def inscribir(self, id_persona: str, template) -> int:
        """Añade una muestra. Devuelve cuántas muestras tiene ya esa persona."""
        id_persona = validar_id(id_persona)
        template = self.extractor.validar_template(template)
        self._obtener_claves(crear=not self.ruta.is_file())
        # Se verifica y decodifica el fichero completo antes de reescribirlo:
        # nunca se vuelve a firmar contenido que no pasó la verificación.
        filas = self._cargar()
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
        filas = self._cargar()  # verifica la integridad antes de reescribir
        quedan = [(id_fila, campo) for id_fila, campo, _ in filas if id_fila != id_persona]
        borradas = len(filas) - len(quedan)
        if borradas == 0:
            raise IdNoInscrito(f"'{id_persona}' no está inscrito")
        self._escribir_filas(quedan)
        return borradas

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
        id_persona = validar_id(id_persona)
        muestras = [(self.clave_muestra(campo), template)
                    for id_fila, campo, template in self._cargar() if id_fila == id_persona]
        if not muestras:
            raise IdNoInscrito(f"'{id_persona}' no está inscrito")
        return muestras

    def eliminar_muestra(self, id_persona: str, clave: str) -> int:
        """Borra una muestra concreta. Devuelve cuántas le quedan a esa persona."""
        id_persona = validar_id(id_persona)
        filas = self._cargar()
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
        filas = self._cargar()
        if not any(i == id_persona for i, _, _ in filas):
            raise IdNoInscrito(f"'{id_persona}' no está inscrito")
        if nuevo != id_persona and any(i == nuevo for i, _, _ in filas):
            raise IdInvalido(f"Ya hay alguien inscrito como '{nuevo}'")
        claves: dict[str, str] = {}
        nuevas = []
        for i, campo, template in filas:
            if i == id_persona:
                nuevo_campo = self._codificar(nuevo, template) if campo.startswith(PREFIJO_CIFRADO) else campo
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
