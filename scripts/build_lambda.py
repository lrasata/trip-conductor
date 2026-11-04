"""Build Lambda packages: a service's source, the shared libs and its runtime dependencies.

    python scripts/build_lambda.py flight     # one service
    python scripts/build_lambda.py --all      # every service, as CI does

Writes services/<service>/build/package/, which Terraform zips. Dependencies are fetched
as Linux arm64 / Python 3.13 wheels to match the Lambda runtime, whatever OS you build on.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def build(service: str) -> Path:
    service_dir = ROOT / "services" / service
    if not (service_dir / "src").is_dir():
        sys.exit(f"unknown service: {service} (expected {service_dir / 'src'})")

    package = service_dir / "build" / "package"
    shutil.rmtree(package, ignore_errors=True)
    shutil.copytree(service_dir / "src", package, ignore=IGNORE)

    # libs/ is the one place code is shared between services (ADR 0005).
    for lib in sorted((ROOT / "libs").glob("*/src")):
        shutil.copytree(lib, package, ignore=IGNORE, dirs_exist_ok=True)

    requirements = service_dir / "requirements.txt"
    if requirements.exists():
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet",
                "--requirement", str(requirements),
                "--target", str(package),
                "--platform", "manylinux2014_aarch64",
                "--implementation", "cp",
                "--python-version", "3.13",
                "--only-binary=:all:",
                "--upgrade",
            ],
            check=True,
        )
    return package


def all_services() -> list[str]:
    return sorted(path.parent.name for path in (ROOT / "services").glob("*/src"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for service in all_services() if sys.argv[1] == "--all" else [sys.argv[1]]:
        print(build(service))
