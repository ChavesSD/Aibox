"""Entrada para PyInstaller / execução direta."""
from __future__ import annotations

import sys


def _boot() -> int:
    if "--update-helper" in sys.argv:
        from aibox.update_helper import main as helper_main

        # remove o flag para o argparse do helper
        argv = [a for a in sys.argv[1:] if a != "--update-helper"]
        return helper_main(argv)

    from aibox.main import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_boot())
