"""Rehace la BD de JumeX Lens con el extractor actual a partir de las fotos de la galería.

Uso:
    python scripts/migrar_extractor.py                         # config_lens.yaml
    python scripts/migrar_extractor.py --config otra.yaml --si

Al cambiar de extractor (p. ej. SFace -> ArcFace) los templates guardados dejan
de valer: tienen otra dimensión y otra escala. El sistema no guarda las fotos
originales, pero la web sí guarda un retrato alineado de cada muestra (galería,
SOLO para mostrar). Este script:

    1. copia la carpeta de la BD entera a <carpeta>_respaldo_<fecha> (BD, clave y galería);
    2. lee las filas de la BD vieja verificando su HMAC (sin decodificar los templates);
    3. por cada muestra con foto: detecta la cara en el retrato (o, si no la
       encuentra, usa la posición conocida de los 5 puntos en el retrato),
       la alinea, extrae el template nuevo y lo inscribe con el mismo id;
    4. mueve cada foto a su clave nueva y conserva la foto principal.

Las muestras sin foto no se pueden migrar: se listan al final.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Permite ejecutar el script sin instalar el paquete
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biometria.bd import BaseDatos  # noqa: E402
from biometria.config import RAIZ_REPO, cargar_config  # noqa: E402
from biometria.detector import PLANTILLA_LANDMARKS_112, alinear, nitidez  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.sistema import SistemaBiometrico  # noqa: E402
from biometria.web.analisis import ESCALA_RETRATO, Galeria, LADO_RETRATO  # noqa: E402


def landmarks_del_retrato(lado: int) -> np.ndarray:
    """Dónde quedan los 5 puntos en un retrato de la galería (inversa de analisis.retrato)."""
    puntos = (PLANTILLA_LANDMARKS_112 / 112.0 - 0.5) * ESCALA_RETRATO * lado + lado / 2
    puntos[:, 1] += lado * 0.04
    return puntos.astype(np.float32)


def cara_del_retrato(sistema: SistemaBiometrico, retrato: np.ndarray) -> np.ndarray:
    """Cara alineada 112x112 lista para el extractor."""
    try:
        return sistema.detector.procesar(retrato).cara
    except ErrorBiometrico:
        pass  # p. ej. retrato muy justo: se usan los puntos conocidos
    if retrato.shape[:2] != (LADO_RETRATO, LADO_RETRATO):
        retrato = cv2.resize(retrato, (LADO_RETRATO, LADO_RETRATO))
    return alinear(retrato, landmarks_del_retrato(LADO_RETRATO), sistema.cfg.preprocesado.tam_cara)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=RAIZ_REPO / "config_lens.yaml")
    parser.add_argument("--si", action="store_true", help="no pedir confirmación")
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    sistema = SistemaBiometrico(cfg)
    ruta_bd = cfg.bd.ruta
    galeria = Galeria(ruta_bd.parent / "caras")
    if not ruta_bd.is_file():
        print(f"No hay BD en {ruta_bd}: nada que migrar")
        return 0

    # Filas viejas con su HMAC verificado. Los templates no se decodifican: son del
    # extractor anterior y no pasarían la validación del actual.
    vieja = BaseDatos(cfg, sistema.extractor)
    try:
        filas = vieja._leer_filas()
    except ErrorBiometrico as e:
        print(f"La BD vieja no pasa la verificación ({e.codigo}): {e.mensaje}", file=sys.stderr)
        return 1
    claves_por_id: dict[str, list[str]] = {}
    for id_persona, campo in filas:
        claves_por_id.setdefault(id_persona, []).append(BaseDatos.clave_muestra(campo))
    galeria.migrar(claves_por_id)  # retratos antiguos (uno por persona) -> foto de muestra

    print(f"{len(filas)} muestras de {len(claves_por_id)} personas en {ruta_bd}")
    print(f"Se van a rehacer con el extractor '{sistema.extractor.nombre}'.")
    if not args.si and input("¿Continuar? [s/N] ").strip().lower() not in ("s", "si", "sí"):
        return 1

    respaldo = ruta_bd.parent.with_name(f"{ruta_bd.parent.name}_respaldo_{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copytree(ruta_bd.parent, respaldo)
    print(f"Respaldo completo en {respaldo}")

    # Las fotos se leen del respaldo: la galería se va reescribiendo con las claves nuevas
    galeria_vieja = Galeria(respaldo / "caras")
    principales = {}
    for id_persona, claves in claves_por_id.items():
        fichero = galeria_vieja.carpeta_persona(id_persona) / "principal"
        if fichero.is_file():
            principales[id_persona] = fichero.read_text(encoding="ascii").strip()
    ruta_bd.unlink()
    for id_persona in claves_por_id:
        shutil.rmtree(galeria.carpeta_persona(id_persona), ignore_errors=True)

    migradas, perdidas = 0, []
    legado_usado = set()
    for id_persona, campo in filas:
        clave_vieja = BaseDatos.clave_muestra(campo)
        foto = galeria_vieja.ruta_muestra(id_persona, clave_vieja)
        if not foto.is_file():
            # Retrato antiguo de una persona con varias muestras: no se sabe de cuál
            # es, pero vale como UNA muestra (mejor que perder a la persona)
            legado = galeria_vieja.ruta_legado(id_persona)
            if legado.is_file() and id_persona not in legado_usado:
                legado_usado.add(id_persona)
                foto = legado
            else:
                perdidas.append((id_persona, "sin foto en la galería"))
                continue
        retrato = cv2.imread(str(foto), cv2.IMREAD_COLOR)
        try:
            cara = cara_del_retrato(sistema, retrato)
            if nitidez(cara) <= 0:
                raise ValueError("cara vacía")
            template = sistema.extractor.extraer(cara)
            sistema.inscribir_t(id_persona, template, firma=sistema.firmar(template))
        except (ErrorBiometrico, ValueError) as e:
            perdidas.append((id_persona, str(e)))
            continue
        clave_nueva = sistema.bd.muestras_de(id_persona)[-1][0]
        destino = galeria.ruta_muestra(id_persona, clave_nueva)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(foto, destino)
        if principales.get(id_persona) == clave_vieja:
            galeria.fijar_principal(id_persona, clave_nueva)
        migradas += 1
        print(f"\r  {migradas} muestras migradas", end="", flush=True)

    print(f"\n{migradas} muestras migradas a '{sistema.extractor.nombre}' "
          f"({sistema.extractor.dimension} valores) · {len(sistema.bd.ids())} personas")
    for id_persona, motivo in perdidas:
        print(f"  [no migrada] {id_persona}: {motivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
