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
from .validation import (
    ProfileValidationIssue,
    ProfileValidationReport,
    dry_run_profile_validation,
    find_unresolved_placeholders,
    validate_generated_profile_files,
    validate_profile_contract,
)

__all__ = [
    "CIConfig",
    "CommandSpec",
    "PreviewConfig",
    "Profile",
    "ProfileError",
    "ProfileNotFoundError",
    "ProfileTemplate",
    "ProfileValidationIssue",
    "ProfileValidationReport",
    "dry_run_profile_validation",
    "find_unresolved_placeholders",
    "list_profile_ids",
    "list_profiles",
    "load_profile",
    "profile_exists",
    "validate_generated_profile_files",
    "validate_profile_contract",
]
