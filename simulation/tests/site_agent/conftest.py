"""Shared fixtures for the Pilot Site Agent service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nxt_site_agent import SiteAgentService
from nxt_workflow_enablement import RANGE_OPS_WORKFLOW_ID
from scripts.site_agent_fixture import (
    DEPLOYMENT_ID,
    SITE_ID,
    broken_service_manifest_payload,
    service_composition_seam,
)


@pytest.fixture()
def seam():
    return service_composition_seam()


@pytest.fixture()
def broken_seam():
    return service_composition_seam(
        payload_provider=broken_service_manifest_payload
    )


@pytest.fixture()
def launch(seam):
    def _launch(
        runs_root: Path, *, force_fresh: bool = False
    ) -> SiteAgentService:
        return SiteAgentService.launch(
            runs_root=runs_root,
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=seam,
            force_fresh=force_fresh,
        )

    return _launch
