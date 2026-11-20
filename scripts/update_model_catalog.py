"""Regenerate the bundled model catalog snapshot from a published pi-ai release.

Usage: uv run python scripts/update_model_catalog.py [version]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_PATH))

from core.model_catalog.pi_package import build_model_catalog, read_package_files
from core.model_catalog.store import BUNDLED_CATALOG_FILE, write_model_catalog
from service.model_catalog.refresh import download_pi_package


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "latest"
    catalog = build_model_catalog(read_package_files(asyncio.run(download_pi_package(version))))
    write_model_catalog(catalog, BUNDLED_CATALOG_FILE)
    models = sum(len(provider.models) for provider in catalog.providers)
    print(
        f"{BUNDLED_CATALOG_FILE.relative_to(ROOT_PATH)}: {catalog.source.package}@{catalog.source.version}, "
        f"{len(catalog.providers)} providers, {models} models"
    )


if __name__ == "__main__":
    main()
