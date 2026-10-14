"""Excepciones del sistema biométrico.

Todas heredan de ErrorBiometrico y tienen un `codigo` corto y estable.
La CLI lo devuelve en su salida JSON, así quien use el sistema (incluido el
grupo rival) puede distinguir los motivos de rechazo sin analizar el texto.
"""


class ErrorBiometrico(Exception):
    """Clase base de todos los errores controlados del sistema."""

    codigo = "error"

    def __init__(self, mensaje: str):
        super().__init__(mensaje)
        self.mensaje = mensaje

    def a_dict(self) -> dict:
        return {"error": self.codigo, "mensaje": self.mensaje}


# --- Configuración ---------------------------------------------------------

class ConfiguracionInvalida(ErrorBiometrico):
    codigo = "configuracion_invalida"


# --- Entrada de imágenes ---------------------------------------------------

class ImagenInvalida(ErrorBiometrico):
    """El fichero no existe, no es una imagen o está corrupto."""

    codigo = "imagen_invalida"


class ImagenDemasiadoGrande(ErrorBiometrico):
    """Supera el límite de bytes o de píxeles (protección frente a DoS)."""

    codigo = "imagen_demasiado_grande"


# --- Detección y control de calidad ----------------------------------------

class SinCara(ErrorBiometrico):
    codigo = "sin_cara"


class VariasCaras(ErrorBiometrico):
    codigo = "varias_caras"


class CaraPequena(ErrorBiometrico):
    codigo = "cara_pequena"


class ImagenBorrosa(ErrorBiometrico):
    codigo = "imagen_borrosa"


# --- Templates e identidades -----------------------------------------------

class TemplateInvalido(ErrorBiometrico):
    """Dimensión, tipo o valores (NaN/inf, rango) no válidos para el extractor."""

    codigo = "template_invalido"


class IdInvalido(ErrorBiometrico):
    codigo = "id_invalido"


class IdNoInscrito(ErrorBiometrico):
    codigo = "id_no_inscrito"


class LimiteMuestras(ErrorBiometrico):
    codigo = "limite_muestras"


class MuestraIncoherente(ErrorBiometrico):
    """La muestra nueva no se parece a las que ya tiene esa persona.

    Defiende la inscripción de un envenenamiento: añadir la cara de un atacante
    como muestra de la víctima para que luego pase por ella.
    """

    codigo = "muestra_incoherente"


class DemasiadosIntentos(ErrorBiometrico):
    """Se ha superado el límite de comparaciones por template (_t) en poco tiempo.

    Frena el hill-climbing: ajustar un template probándolo muchas veces hasta
    superar el umbral, usando la puntuación como guía.
    """

    codigo = "demasiados_intentos"


# --- Base de datos ---------------------------------------------------------

class BDCorrupta(ErrorBiometrico):
    """El CSV no tiene el formato esperado."""

    codigo = "bd_corrupta"


class IntegridadBD(ErrorBiometrico):
    """El HMAC no cuadra: el CSV ha sido modificado fuera del sistema."""

    codigo = "integridad_bd"


class MuestraNoEncontrada(ErrorBiometrico):
    codigo = "muestra_no_encontrada"
