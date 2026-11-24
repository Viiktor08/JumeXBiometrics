"""Interfaz de línea de comandos: `python -m biometria <comando> ...`

    python -m biometria inscribir     --id "Ana López" --foto ana.jpg
    python -m biometria verificar     --id "Ana López" --foto otra.jpg
    python -m biometria identificar   --foto otra.jpg
    python -m biometria inscribir_sd  --id "Ana López" --foto cara_recortada.png
    python -m biometria verificar_sd  --id "Ana López" --foto cara_recortada.png
    python -m biometria identificar_sd --foto cara_recortada.png
    python -m biometria inscribir_t   --id "Ana López" --template ana.npy
    python -m biometria verificar_t   --id "Ana López" --template ana.npy
    python -m biometria identificar_t --template ana.npy

    python -m biometria extraer --foto ana.jpg --salida ana.npy [--sin-detector]
    python -m biometria bd           # integridad y personas inscritas
    python -m biometria estado       # versiones y entorno (también sin comando)

La salida es siempre UNA línea JSON en stdout. Código de salida: 0 si la
operación se ha ejecutado (aunque la verificación sea rechazada: mira
"aceptado"), 1 si hay un error controlado ({"error": codigo, "mensaje": ...}),
2 si los argumentos son incorrectos.

Templates (--template): .npy (float32, sin pickle), .json (lista de números) o
un fichero de texto con el formato de la BD ("f32:" + base64).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

import numpy as np

from biometria import __version__
from biometria.config import cargar_config
from biometria.errores import ErrorBiometrico, TemplateInvalido

# Un template LBP ocupa ~12 KB en .npy; 1 MiB deja margen para cualquier extractor.
MAX_BYTES_TEMPLATE = 1024 * 1024


def cargar_template(ruta: Path) -> np.ndarray:
    """Lee un template de fichero (.npy, .json o texto "f32:base64")."""
    if not ruta.is_file():
        raise TemplateInvalido(f"No existe el fichero de template: {ruta}")
    if ruta.stat().st_size > MAX_BYTES_TEMPLATE:
        raise TemplateInvalido(f"El fichero de template supera {MAX_BYTES_TEMPLATE} bytes")
    try:
        if ruta.suffix.lower() == ".npy":
            # allow_pickle=False: un .npy con objetos Python no se deserializa nunca.
            return np.load(ruta, allow_pickle=False)
        texto = ruta.read_text(encoding="utf-8").strip()
        if ruta.suffix.lower() == ".json":
            return np.asarray(json.loads(texto), dtype=np.float32)
        texto = texto.removeprefix("f32:")
        return np.frombuffer(base64.b64decode(texto, validate=True), dtype="<f4")
    except (OSError, TypeError, ValueError, UnicodeDecodeError, binascii.Error) as e:
        raise TemplateInvalido(f"No se pudo leer el template de {ruta}: {e}") from e


def guardar_template(template: np.ndarray, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.suffix.lower() == ".json":
        ruta.write_text(json.dumps(template.astype(float).tolist()), encoding="utf-8")
    elif ruta.suffix.lower() == ".npy":
        np.save(ruta, template.astype("<f4"), allow_pickle=False)
    else:
        ruta.write_text("f32:" + base64.b64encode(template.astype("<f4").tobytes()).decode(),
                        encoding="utf-8")


def _estado(ruta_config: str | None) -> dict:
    import cv2

    cfg = cargar_config(ruta_config)
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "modelo_detector": str(cfg.detector.modelo),
        "modelo_detector_presente": cfg.detector.modelo.is_file(),
        "extractor": cfg.extractor.nombre,
        "bd": str(cfg.bd.ruta),
        "bd_existe": cfg.bd.ruta.is_file(),
    }


def _crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m biometria",
        description="JumeXBiometrics: reconocimiento facial (salida en JSON)",
    )
    parser.add_argument("--config", default=None, help="fichero de configuración alternativo")
    sub = parser.add_subparsers(dest="comando", metavar="comando")

    for sufijo, ayuda in (("", "pipeline completo"), ("_sd", "sin detector: la foto ya es una cara")):
        p = sub.add_parser(f"inscribir{sufijo}", help=f"inscribe a una persona ({ayuda})")
        p.add_argument("--id", required=True)
        p.add_argument("--foto", required=True, type=Path)
        p = sub.add_parser(f"verificar{sufijo}", help=f"verificación 1:1 ({ayuda})")
        p.add_argument("--id", required=True)
        p.add_argument("--foto", required=True, type=Path)
        p = sub.add_parser(f"identificar{sufijo}", help=f"identificación 1:N ({ayuda})")
        p.add_argument("--foto", required=True, type=Path)

    ayuda_t = "sin detector ni extractor: recibe un template"
    ayuda_firma = "firma HMAC del template (por defecto <template>.sig si existe)"
    p = sub.add_parser("inscribir_t", help=f"inscribe a una persona ({ayuda_t})")
    p.add_argument("--id", required=True)
    p.add_argument("--template", required=True, type=Path)
    p.add_argument("--firma", default=None, help=ayuda_firma)
    p = sub.add_parser("verificar_t", help=f"verificación 1:1 ({ayuda_t})")
    p.add_argument("--id", required=True)
    p.add_argument("--template", required=True, type=Path)
    p.add_argument("--firma", default=None, help=ayuda_firma)
    p = sub.add_parser("identificar_t", help=f"identificación 1:N ({ayuda_t})")
    p.add_argument("--template", required=True, type=Path)
    p.add_argument("--firma", default=None, help=ayuda_firma)

    p = sub.add_parser("extraer", help="guarda el template de una foto (.npy, .json o texto)")
    p.add_argument("--foto", required=True, type=Path)
    p.add_argument("--salida", required=True, type=Path)
    p.add_argument("--sin-detector", action="store_true", help="como los métodos _sd")

    sub.add_parser("bd", help="verifica la integridad de la BD y lista los inscritos")
    sub.add_parser("sellar", help="acepta la BD actual como la buena y crea su sello anti-rollback")
    sub.add_parser("estado", help="versiones y estado del entorno")
    return parser


def ejecutar(args: argparse.Namespace) -> dict:
    """Ejecuta un comando ya parseado y devuelve el resultado como dict."""
    if args.comando in (None, "estado"):
        return _estado(args.config)

    # Import diferido: `estado` debe funcionar aunque falte el modelo o la BD esté rota.
    from biometria.sistema import SistemaBiometrico

    sistema = SistemaBiometrico(cargar_config(args.config))
    comando = args.comando
    if comando == "extraer":
        template = sistema.extraer_sd(args.foto) if args.sin_detector else sistema.extraer(args.foto)
        guardar_template(template, args.salida)
        # Firma HMAC junto al template (<salida>.sig): permite a los _t saber que lo
        # generó este sistema y no un atacante. Ver THREAT_MODEL.md (paso 2).
        ruta_firma = args.salida.with_name(args.salida.name + ".sig")
        ruta_firma.write_text(sistema.firmar(template), encoding="ascii")
        return {"operacion": "extraer", "salida": str(args.salida), "firma": str(ruta_firma),
                "dimension": int(template.size)}
    if comando == "sellar":
        filas = sistema.bd.sellar()
        return {"operacion": "sellar", "ruta": str(sistema.bd.ruta), "muestras": filas,
                "sello": str(sistema.bd.ruta_sello())}
    if comando == "bd":
        agrupadas = sistema.bd.por_id()
        d = sistema.cfg.defensa
        return {"operacion": "bd", "integridad": "ok", "ruta": str(sistema.bd.ruta),
                "personas": len(agrupadas), "muestras": sum(len(t) for t in agrupadas.values()),
                "protecciones": {"cifrado": sistema.cfg.bd.cifrar_templates,
                                 "anti_rollback": sistema.cfg.bd.anti_rollback,
                                 "sello_estricto": sistema.cfg.bd.sello_estricto,
                                 "clave_junto_a_bd": sistema.bd.clave_junto_a_bd(),
                                 "exigir_firma_t": d.exigir_firma_t,
                                 "margen_identificacion": d.margen_identificacion,
                                 "cara_en_sd": d.cara_en_sd,
                                 "coherencia_muestra": d.coherencia_muestra,
                                 "ancla_muestra": d.ancla_muestra,
                                 "cara_duplicada": d.cara_duplicada},
                "ids": {id_persona: len(t) for id_persona, t in sorted(agrupadas.items())}}

    metodo = getattr(sistema, comando)
    if comando.endswith("_t"):
        dato = cargar_template(args.template)
        # Firma: la que se pase con --firma, o un fichero <template>.sig al lado
        firma = args.firma
        if firma is None:
            lado = args.template.with_name(args.template.name + ".sig")
            firma = lado.read_text(encoding="ascii").strip() if lado.is_file() else None
        if comando.startswith("identificar"):
            resultado = metodo(dato, firma)
        else:
            resultado = metodo(args.id, dato, firma)
    elif comando.startswith("identificar"):
        resultado = metodo(args.foto)
    else:
        resultado = metodo(args.id, args.foto)
    return {**resultado.a_dict(), "operacion": comando}


def main(argv: list[str] | None = None) -> int:
    args = _crear_parser().parse_args(argv)
    try:
        salida = ejecutar(args)
        codigo = 0
    except ErrorBiometrico as e:
        salida = e.a_dict()
        codigo = 1
    print(json.dumps(salida, ensure_ascii=False, indent=2 if args.comando in (None, "estado", "bd") else None))
    return codigo
