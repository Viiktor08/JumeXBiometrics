"""Pone una base de datos existente al día con las defensas del paso 6 (THREAT_MODEL.md).

Uso:
    python scripts/proteger_bd.py --config config_lens.yaml --clave-antigua data/lens/clave_hmac.key
    python scripts/proteger_bd.py --config config.yaml      --clave-antigua data/clave_hmac.key

Hace, en este orden:
1. Mueve la clave maestra de su sitio antiguo (junto a la BD, en data/) a
   `bd.ruta_clave` de la configuración (por defecto ~/.jumex/, fuera del
   repositorio y de la carpeta de datos). Permisos 0600. Si la clave ya está en
   su sitio nuevo, no toca nada.
2. Reescribe la BD con la codificación de la configuración: con
   `cifrar_templates: true`, cifra con AES-GCM las muestras que estaban en claro.
   Las fotos de la galería de la web se renombran a la vez (su nombre depende
   del campo cifrado).
3. Al escribir, crea el sello anti-rollback (si `anti_rollback: true`).

Antes de nada verifica la BD entera (HMAC fila a fila): si está manipulada, para.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biometria.config import cargar_config  # noqa: E402
from biometria.errores import ErrorBiometrico  # noqa: E402
from biometria.sistema import SistemaBiometrico  # noqa: E402
from biometria.web.analisis import Galeria  # noqa: E402


def mover_clave(antigua: Path, nueva: Path) -> str:
    if nueva.is_file():
        if antigua.is_file() and antigua.read_bytes() != nueva.read_bytes():
            raise SystemExit(f"Hay dos claves distintas: {antigua} y {nueva}. Decide cuál es la buena.")
        if antigua.is_file():
            antigua.unlink()
            return f"la clave ya estaba en {nueva}; borrada la copia de {antigua}"
        return f"la clave ya está en {nueva}"
    if not antigua.is_file():
        raise SystemExit(f"No existe la clave antigua {antigua} ni la nueva {nueva}")
    nueva.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(nueva.parent, 0o700)
    shutil.copyfile(antigua, nueva)
    os.chmod(nueva, 0o600)
    if nueva.read_bytes() != antigua.read_bytes():
        raise SystemExit("La copia de la clave no coincide: no se borra la antigua")
    antigua.unlink()
    return f"clave movida de {antigua} a {nueva}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--clave-antigua", type=Path, default=None,
                        help="dónde estaba la clave antes (p. ej. data/lens/clave_hmac.key)")
    args = parser.parse_args()
    cfg = cargar_config(args.config)

    if args.clave_antigua is not None:
        print(mover_clave(args.clave_antigua.resolve(), cfg.bd.ruta_clave))
    sistema = SistemaBiometrico(cfg)
    try:
        cambios = sistema.bd.recodificar()
    except ErrorBiometrico as e:
        print(f"La BD no se ha tocado: [{e.codigo}] {e.mensaje}")
        return 1
    galeria = Galeria(cfg.bd.ruta.parent / "caras")
    movidas = 0
    for id_persona, claves in cambios.items():
        galeria.cambiar_claves(id_persona, claves)
        movidas += sum(a != b for a, b in claves.items())
    muestras = sum(len(c) for c in cambios.values())
    print(f"BD {cfg.bd.ruta}: {len(cambios)} personas, {muestras} muestras; "
          f"{movidas} recodificadas ({'cifradas' if cfg.bd.cifrar_templates else 'en claro'})")
    if cfg.bd.anti_rollback:
        print(f"Sello anti-rollback: {sistema.bd.ruta_sello()}")
    if sistema.bd.clave_junto_a_bd():
        print("AVISO: la clave sigue en la misma carpeta que la BD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
