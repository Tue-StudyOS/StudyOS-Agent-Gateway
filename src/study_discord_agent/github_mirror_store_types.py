from dataclasses import dataclass

from study_discord_agent.github_mirror_model import GitHubMirrorRecord


class GitHubMirrorStoreCorruptionError(RuntimeError):
    pass


class GitHubDeliveryCollision(RuntimeError):
    pass


class GitHubMirrorRevisionConflict(RuntimeError):
    pass


class GitHubMirrorMutationReentryError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubMirrorUpsert:
    record: GitHubMirrorRecord
    duplicate: bool
