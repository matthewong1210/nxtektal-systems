"""One-time composition seam from commissioned truth into Site Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nxt_telemetry.observations import SiteConfig

if TYPE_CHECKING:
    from nxt_commissioning.contracts import CommissionedSite
    from nxt_commissioning.projections import LegacySiteConfigContext
else:
    CommissionedSite = Any
    LegacySiteConfigContext = Any


@dataclass(frozen=True)
class RuntimeSiteBinding:
    """Identity and telemetry config projected from one CommissionedSite."""

    site_id: str
    deployment_id: str
    site_config: SiteConfig


def bind_commissioned_site(
    site: CommissionedSite,
    context: LegacySiteConfigContext,
) -> RuntimeSiteBinding:
    """Use commissioning's existing validated compatibility projection.

    Imports are deliberately lazy so the hot runtime and its contract surface
    do not require the optional commissioning package after setup.
    """
    from nxt_commissioning.projections import project_legacy_site_config

    payload = project_legacy_site_config(site, context)
    return RuntimeSiteBinding(
        site_id=site.site_id,
        deployment_id=site.deployment_id,
        site_config=SiteConfig(**payload),
    )
