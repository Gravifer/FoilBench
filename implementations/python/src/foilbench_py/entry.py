"""Console bootstrap that can enable runtime array checks before importing the app."""

import os
import sys

from jaxtyping import install_import_hook


def main() -> None:
    typecheck = "--typecheck" in sys.argv or os.environ.get("FOILBENCH_TYPECHECK") == "1"
    if "--typecheck" in sys.argv:
        sys.argv.remove("--typecheck")
    if typecheck:
        with install_import_hook("foilbench_py", "beartype.beartype"):
            from foilbench_py.cli import main as cli_main

            cli_main()
    else:
        from foilbench_py.cli import main as cli_main

        cli_main()
