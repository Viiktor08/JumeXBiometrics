"""`python -m biometria.web`: arranca JumeX Lens en http://127.0.0.1:8000."""

import sys

from biometria.web.servidor import main

if __name__ == "__main__":
    sys.exit(main())
