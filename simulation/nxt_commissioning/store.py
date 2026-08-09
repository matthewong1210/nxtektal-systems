"""Canonical JSON persistence for authoritative commissioning manifests."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .contracts import CommissionedSite
from .validation import validate_commissioned_site

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DuplicateJSONKeyError(ValueError):
    """JSON contained the same object key more than once."""


class ManifestExistsError(FileExistsError):
    """A different immutable manifest already occupies this deployment ID."""


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def dumps_manifest(site: CommissionedSite) -> str:
    """Return canonical UTF-8 JSON with one trailing newline."""

    validate_commissioned_site(site)
    return (
        json.dumps(
            site.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def loads_manifest(document: str | bytes | bytearray) -> CommissionedSite:
    """Strictly decode and validate one commissioning manifest."""

    if isinstance(document, (bytes, bytearray)):
        document = bytes(document).decode("utf-8")
    if not isinstance(document, str):
        raise TypeError("commissioning manifest document must be str or UTF-8 bytes")
    payload = json.loads(document, object_pairs_hook=_reject_duplicate_object_keys)
    if not isinstance(payload, dict):
        raise ValueError("commissioning manifest root must be a JSON object")
    site = CommissionedSite.from_dict(payload)
    validate_commissioned_site(site)
    return site


def _validate_store_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            f"{field_name} must be a safe commissioning identifier without path separators"
        )


class CommissioningStore:
    """Filesystem store keyed by immutable ``(site_id, deployment_id)``."""

    def __init__(self, root: str | Path):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, site_id: str, deployment_id: str) -> Path:
        _validate_store_id(site_id, "site_id")
        _validate_store_id(deployment_id, "deployment_id")
        return self._root / site_id / deployment_id / "manifest.json"

    def _prepare_parent(self, target: Path) -> None:
        """Create store directories without following identity symlinks."""

        self._root.mkdir(parents=True, exist_ok=True)
        resolved_root = self._root.resolve(strict=True)
        current = self._root
        relative_parent = target.parent.relative_to(self._root)
        for component in relative_parent.parts:
            current = current / component
            if current.is_symlink():
                raise ValueError(
                    f"commissioning store path contains symlink component: {current}"
                )
            if current.exists() and not current.is_dir():
                raise ValueError(
                    f"commissioning store path component is not a directory: {current}"
                )
            current.mkdir(exist_ok=True)
        if not current.resolve(strict=True).is_relative_to(resolved_root):
            raise ValueError("commissioning store path escapes configured root")
        if target.is_symlink():
            raise ValueError("commissioning manifest target must not be a symlink")

    def _check_existing_path(self, target: Path) -> None:
        resolved_root = self._root.resolve(strict=True)
        current = self._root
        for component in target.relative_to(self._root).parts:
            current = current / component
            if current.is_symlink():
                raise ValueError(
                    f"commissioning store path contains symlink component: {current}"
                )
        if not target.resolve(strict=True).is_relative_to(resolved_root):
            raise ValueError("commissioning store path escapes configured root")

    def save(self, site: CommissionedSite) -> Path:
        """Atomically create a manifest, refusing conflicting replacement.

        Saving identical bytes is idempotent.  A different manifest for the
        same deployment identity raises ``ManifestExistsError``; callers must
        commission a new deployment ID rather than mutate history in place.
        """

        validate_commissioned_site(site)
        target = self.path_for(site.site_id, site.deployment_id)
        encoded = dumps_manifest(site).encode("utf-8")
        self._prepare_parent(target)

        if target.exists():
            if target.read_bytes() == encoded:
                return target
            raise ManifestExistsError(f"commissioning manifest already exists: {target}")

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=".manifest-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                if target.read_bytes() == encoded:
                    return target
                raise ManifestExistsError(
                    f"commissioning manifest already exists: {target}"
                ) from None
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return target

    def load(self, site_id: str, deployment_id: str) -> CommissionedSite:
        path = self.path_for(site_id, deployment_id)
        self._check_existing_path(path)
        site = loads_manifest(path.read_bytes())
        if site.site_id != site_id or site.deployment_id != deployment_id:
            raise ValueError(
                "stored manifest identity does not match its site/deployment path"
            )
        return site


__all__ = [
    "CommissioningStore",
    "DuplicateJSONKeyError",
    "ManifestExistsError",
    "dumps_manifest",
    "loads_manifest",
]
