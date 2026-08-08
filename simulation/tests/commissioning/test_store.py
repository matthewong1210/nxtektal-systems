"""Canonical, strict, immutable manifest persistence."""

from __future__ import annotations

import json

import pytest

from nxt_commissioning import (
    CommissionedSite,
    CommissioningStore,
    DuplicateJSONKeyError,
    ManifestExistsError,
    dumps_manifest,
    loads_manifest,
)

from .conftest import clone_payload


def test_canonical_manifest_text_roundtrips_byte_identically(commissioned_site):
    document = dumps_manifest(commissioned_site)
    assert document.endswith("\n")
    assert loads_manifest(document) == commissioned_site
    assert dumps_manifest(loads_manifest(document)) == document
    assert json.loads(document)["schema"] == "nxt-commissioning/manifest/v0"


def test_manifest_loader_accepts_utf8_bytes(commissioned_site):
    encoded = dumps_manifest(commissioned_site).encode("utf-8")
    assert loads_manifest(encoded) == commissioned_site


def test_duplicate_json_object_keys_are_rejected():
    with pytest.raises(DuplicateJSONKeyError, match="schema"):
        loads_manifest('{"schema":"one","schema":"two"}')


@pytest.mark.parametrize(
    "document",
    [
        "not-json",
        "[]",
        '{"schema":"nxt-commissioning/manifest/v99"}',
    ],
)
def test_malformed_or_unsupported_documents_fail_loudly(document):
    with pytest.raises((json.JSONDecodeError, TypeError, ValueError)):
        loads_manifest(document)


def test_store_save_load_and_idempotent_repeat(tmp_path, commissioned_site):
    store = CommissioningStore(tmp_path)
    expected = store.path_for(
        commissioned_site.site_id, commissioned_site.deployment_id
    )
    first = store.save(commissioned_site)
    before = first.read_bytes()
    second = store.save(commissioned_site)

    assert first == expected == second
    assert first.read_text(encoding="utf-8") == dumps_manifest(commissioned_site)
    assert second.read_bytes() == before
    assert store.load(
        commissioned_site.site_id, commissioned_site.deployment_id
    ) == commissioned_site
    first.resolve().relative_to(tmp_path.resolve())
    assert not list(first.parent.glob(".manifest-*.tmp"))


def test_store_refuses_conflicting_manifest_for_deployment(
    tmp_path, commissioned_site, commissioned_site_payload
):
    store = CommissioningStore(tmp_path)
    store.save(commissioned_site)
    changed_payload = clone_payload(commissioned_site_payload)
    changed_payload["facility_name"] = "A different commissioned facility name"
    changed = type(commissioned_site).from_dict(changed_payload)

    with pytest.raises(ManifestExistsError):
        store.save(changed)


@pytest.mark.parametrize(
    "unsafe", ["../escape", "/absolute", "nested/path", ".", "..", "with space"]
)
def test_store_rejects_unsafe_identifiers(tmp_path, unsafe):
    store = CommissioningStore(tmp_path)
    with pytest.raises(ValueError, match="identifier"):
        store.path_for(unsafe, "deployment-01")
    with pytest.raises(ValueError, match="identifier"):
        store.path_for("site-01", unsafe)


def test_store_load_revalidates_tampered_manifest(tmp_path, commissioned_site):
    store = CommissioningStore(tmp_path)
    path = store.save(commissioned_site)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sensor_bindings"][0]["associated_asset_id"] = "missing-asset"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="associated_asset"):
        store.load(commissioned_site.site_id, commissioned_site.deployment_id)


def test_store_load_rejects_manifest_identity_mismatch(tmp_path, commissioned_site):
    store = CommissioningStore(tmp_path)
    path = store.save(commissioned_site)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["deployment_id"] = "different-deployment"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        store.load(commissioned_site.site_id, commissioned_site.deployment_id)


def test_store_rejects_symlink_escape(tmp_path, commissioned_site):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / commissioned_site.site_id).symlink_to(
        outside, target_is_directory=True
    )
    store = CommissioningStore(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        store.save(commissioned_site)
    assert not (outside / commissioned_site.deployment_id).exists()


def test_canonical_manifest_ignores_unordered_authoring_order(
    commissioned_site_payload,
):
    first_payload = clone_payload(commissioned_site_payload)
    first_payload["operating_constraints"][0]["applies_to"] = [
        "site",
        "robot-01",
    ]
    second_payload = clone_payload(first_payload)
    second_payload["operating_constraints"][0]["applies_to"].reverse()
    second_payload["equipment_assets"].reverse()
    second_payload["robot_assets"][0]["capabilities"].reverse()
    second_payload["robot_assets"][0]["charging_requirements"][
        "electrical_requirements"
    ].reverse()
    second_payload["spatial_reference"]["geometry_references"].reverse()

    assert dumps_manifest(CommissionedSite.from_dict(first_payload)) == dumps_manifest(
        CommissionedSite.from_dict(second_payload)
    )
