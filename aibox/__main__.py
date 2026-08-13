import sys


def _boot() -> int:
    if "--update-helper" in sys.argv:
        from .update_helper import main as helper_main

        argv = [a for a in sys.argv[1:] if a != "--update-helper"]
        return helper_main(argv)

    from .main import main

    return main()


raise SystemExit(_boot())
