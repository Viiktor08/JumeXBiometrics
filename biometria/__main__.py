"""Punto de entrada: `python -m biometria ...` (también el ENTRYPOINT de Docker).

Ver biometria/cli.py para la lista de comandos.
"""

import sys

from biometria.cli import main

if __name__ == "__main__":
    sys.exit(main())
