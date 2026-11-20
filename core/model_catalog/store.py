"""The model catalog on disk: the snapshot shipped with the code and the copy refreshed at runtime."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import WORKSPACE
from schema.model_catalog.catalog import ModelCatalog


BUNDLED_CATALOG_FILE = Path(__file__).with_name("snapshot.json")
REFRESHED_CATALOG_FILE = WORKSPACE / "model_catalog.json"


def load_model_catalog() -> ModelCatalog:
    """Return the newer of the bundled snapshot and the refreshed copy."""
    catalog = read_model_catalog(BUNDLED_CATALOG_FILE)
    if REFRESHED_CATALOG_FILE.exists():
        refreshed = read_model_catalog(REFRESHED_CATALOG_FILE)
        if refreshed.source.generated_at > catalog.source.generated_at:
            return refreshed
    return catalog


def save_refreshed_model_catalog(catalog: ModelCatalog) -> None:
    write_model_catalog(catalog, REFRESHED_CATALOG_FILE)


def read_model_catalog(path: Path) -> ModelCatalog:
    return ModelCatalog.model_validate_json(path.read_bytes())


def write_model_catalog(catalog: ModelCatalog, path: Path) -> None:
    """Atomically write a catalog with one model per line, so snapshot updates diff by model."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(_dump_model_catalog(catalog))
        # The catalog is public data; the temporary file is created private.
        temp_path.chmod(0o644)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _dump_model_catalog(catalog: ModelCatalog) -> str:
    data = catalog.model_dump(mode="json")
    lines = ["{", f'"source": {_dumps(data["source"])},', '"providers": [']
    providers = data["providers"]
    for provider_index, provider in enumerate(providers):
        lines.append(f'{{"id": {_dumps(provider["id"])}, "name": {_dumps(provider["name"])}, "models": [')
        models = provider["models"]
        lines.extend(
            f"{_dumps(model)}{',' if model_index < len(models) - 1 else ''}"
            for model_index, model in enumerate(models)
        )
        lines.append(f"]}}{',' if provider_index < len(providers) - 1 else ''}")
    lines.extend(["]", "}", ""])
    return "\n".join(lines)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
