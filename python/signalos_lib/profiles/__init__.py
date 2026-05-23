"""SignalOS factory profile manifests.

Profiles describe the product-stack assumptions that factory, validation,
preview, and release-readiness code can share without hard-coded branches.
"""

from __future__ import annotations

from .loader import (
    CIConfig,
    CommandSpec,
    PreviewConfig,
    Profile,
    ProfileError,
    ProfileNotFoundError,
    ProfileTemplate,
    list_profile_ids,
    list_profiles,
    load_profile,
    profile_exists,
)

__all__ = [
    "CIConfig",
    "CommandSpec",
    "PreviewConfig",
    "Profile",
    "ProfileError",
    "ProfileNotFoundError",
    "ProfileTemplate",
    "list_profile_ids",
    "list_profiles",
    "load_profile",
    "profile_exists",
]
