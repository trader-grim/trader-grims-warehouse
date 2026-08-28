"""Goal profiles belonging solely to the development/coding domain."""

from dataclasses import dataclass

from tgw.workflow_kernel.contracts import GoalProfile


@dataclass(frozen=True)
class ProfileMeta:
    """Evidence metadata for a development-domain goal profile."""

    description: str
    evidence_source_class: str
    accepted_results: tuple[tuple[str, str], ...] = ()

CODING_READY_FOR_IMPLEMENTATION = GoalProfile(
    identity="coding.ready_for_implementation", version="1",
    required=("implemented", "tested", "linted"),
)
CODING_READY_FOR_REVIEW = GoalProfile(
    identity="coding.ready_for_review", version="1",
    required=("implemented", "tested", "linted"),
)
CODING_DIAGNOSTIC_REVIEW = GoalProfile(
    identity="coding.diagnostic_review", version="1",
    required=("implemented", "tested", "linted", "reviewed", "controller_verified"),
)
CODING_READY_FOR_ADMISSION = GoalProfile(
    identity="coding.ready_for_admission", version="1",
    required=("implemented", "tested", "linted", "reviewed", "controller_verified"),
)
CODING_DEPLOYED = GoalProfile(
    identity="coding.deployed", version="1",
    required=("implemented", "tested", "linted", "reviewed", "controller_verified", "admitted", "committed", "deployed"),
)

PROFILE: dict[str, GoalProfile] = {
    profile.identity: profile
    for profile in (
        CODING_READY_FOR_IMPLEMENTATION,
        CODING_READY_FOR_REVIEW,
        CODING_READY_FOR_ADMISSION,
        CODING_DEPLOYED,
    )
}

PROFILE_META: dict[str, ProfileMeta] = {
    "coding.ready_for_implementation": ProfileMeta(
        description="Code is implemented, tested, and linted for implementation verification.",
        evidence_source_class="ci_pipeline",
    ),
    "coding.ready_for_review": ProfileMeta(
        description="Code is implemented, tested, and linted for peer review.",
        evidence_source_class="ci_pipeline",
    ),
    "coding.ready_for_admission": ProfileMeta(
        description="Code passed review and controller verification for admission.",
        evidence_source_class="review_system",
    ),
    "coding.deployed": ProfileMeta(
        description="Code is admitted, committed, and deployed.",
        evidence_source_class="deployment_system",
    ),
}


def get_profile(identity: str) -> GoalProfile:
    return PROFILE[identity]


def get_meta(identity: str) -> ProfileMeta:
    return PROFILE_META[identity]


def all_profiles() -> tuple[GoalProfile, ...]:
    return tuple(PROFILE.values())

def coding_profiles() -> tuple[GoalProfile, ...]:
    return all_profiles()
