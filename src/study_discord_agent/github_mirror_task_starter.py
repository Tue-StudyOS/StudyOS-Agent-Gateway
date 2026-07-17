from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import discord

from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_threads import channel_context
from study_discord_agent.github_mirror_action_store import (
    GitHubMirrorActionBusy,
    GitHubMirrorActionStore,
    GitHubMirrorActionUnavailable,
)
from study_discord_agent.github_mirror_discord import ItemThread, resolve_or_create_thread
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubMirrorAction,
    GitHubMirrorRecord,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.github_task_context import resolve_github_task_context
from study_discord_agent.github_task_prompts import build_github_task_prompt


class _TaskStore(Protocol):
    def get(self, task_id: str) -> DiscordTaskRecord: ...


class _TaskService(Protocol):
    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord: ...


_INTENTS = {
    GitHubMirrorAction.REVIEW: DiscordTaskIntent.REVIEW,
    GitHubMirrorAction.SECURITY_REVIEW: DiscordTaskIntent.SECURITY_REVIEW,
    GitHubMirrorAction.VULNERABILITY_SCAN: DiscordTaskIntent.VULNERABILITY_SCAN,
    GitHubMirrorAction.WORK: DiscordTaskIntent.IMPLEMENTATION,
}


class GitHubMirrorTaskStarter:
    def __init__(
        self,
        client: discord.Client,
        mirrors: GitHubMirrorStore,
        tasks: _TaskStore,
        service: _TaskService,
        canonical_root: Path,
    ) -> None:
        self._client = client
        self._mirrors = mirrors
        self._actions = GitHubMirrorActionStore(mirrors)
        self._tasks = tasks
        self._service = service
        self._canonical_root = canonical_root

    async def start(
        self,
        action: GitHubMirrorAction,
        record: GitHubMirrorRecord,
        card: discord.Message,
        guild: discord.Guild,
        actor: object,
        trigger_id: int,
        instruction: str | None,
    ) -> str:
        active = self._active_task(record)
        if active is not None:
            return task_response("This item already has a task", active.task_id, record.thread_id)
        record = self._clear_stale_active(record)
        task_id = str(uuid4())
        try:
            reservation = self._actions.reserve(record.mirror_id, trigger_id, action, task_id)
        except GitHubMirrorActionBusy as busy:
            return self._busy_response(record.mirror_id, busy.task_id)
        if not reservation.accepted:
            return self._replayed_response(reservation.record, reservation.task_id)
        try:
            record = reservation.record
            if record.card_message_id != card.id:
                raise GitHubMirrorActionUnavailable("The GitHub card changed. Try again.")
            thread = await resolve_or_create_thread(self._client, record, card, guild, actor)
            record = self._actions.attach_thread(record.mirror_id, task_id, thread.id)
            context = await resolve_github_task_context(record, self._canonical_root)
            request = _task_request(
                record,
                context.commit_sha,
                thread,
                action,
                actor_id=_actor_id(actor),
                trigger_id=trigger_id,
                task_id=task_id,
                prompt=build_github_task_prompt(context, action, instruction),
            )
            started = await self._service.start(request)
            if started.task_id != task_id:
                raise RuntimeError("Discord returned the wrong task identity.")
            self._actions.finish(record.mirror_id, task_id, succeeded=True)
        except BaseException:
            self._settle_failed_start(record.mirror_id, task_id)
            raise
        return task_response(f"Started `{action.value}`", task_id, thread.id)

    async def reconcile_startup(self) -> None:
        for snapshot in self._mirrors.records():
            record = self._mirrors.get(snapshot.mirror_id)
            pending = record.pending_action
            if pending is not None:
                task = self._task(pending.task_id)
                record = self._actions.finish(
                    record.mirror_id,
                    pending.task_id,
                    succeeded=task is not None,
                )
            task = self._task(record.active_task_id)
            if record.active_task_id is not None and (
                task is None or task.state not in ACTIVE_STATES
            ):
                self._actions.clear_active(record.mirror_id, record.active_task_id)

    def _busy_response(self, mirror_id: str, task_id: str) -> str:
        record = self._mirrors.get(mirror_id)
        task = self._task(task_id)
        if record.pending_action is not None:
            if task is not None:
                self._actions.finish(mirror_id, task_id, succeeded=True)
                return task_response("This item already has a task", task_id, record.thread_id)
            return task_response("A task is already starting", task_id, record.thread_id)
        if task is not None and task.state in ACTIVE_STATES:
            return task_response("This item already has a task", task_id, record.thread_id)
        if record.active_task_id == task_id:
            self._actions.clear_active(mirror_id, task_id)
        raise GitHubMirrorActionUnavailable("The previous task ended. Press the action again.")

    def _replayed_response(self, record: GitHubMirrorRecord, task_id: str) -> str:
        task = self._task(task_id)
        if task is not None:
            if record.pending_action is not None:
                self._actions.finish(record.mirror_id, task_id, succeeded=True)
            return task_response("This item already has a task", task_id, record.thread_id)
        prefix = "This action is already starting" if record.pending_action else "Already handled"
        return task_response(prefix, task_id, record.thread_id)

    def _settle_failed_start(self, mirror_id: str, task_id: str) -> None:
        with suppress(GitHubMirrorActionUnavailable):
            self._actions.finish(mirror_id, task_id, succeeded=self._task(task_id) is not None)

    def _active_task(self, record: GitHubMirrorRecord) -> DiscordTaskRecord | None:
        task = self._task(record.active_task_id)
        return task if task is not None and task.state in ACTIVE_STATES else None

    def _clear_stale_active(self, record: GitHubMirrorRecord) -> GitHubMirrorRecord:
        if record.active_task_id is None or self._active_task(record) is not None:
            return record
        return self._actions.clear_active(record.mirror_id, record.active_task_id)

    def _task(self, task_id: str | None) -> DiscordTaskRecord | None:
        if task_id is None:
            return None
        try:
            return self._tasks.get(task_id)
        except KeyError:
            return None


def _task_request(
    record: GitHubMirrorRecord,
    commit_sha: str,
    thread: ItemThread,
    action: GitHubMirrorAction,
    *,
    actor_id: int,
    trigger_id: int,
    task_id: str,
    prompt: str,
) -> DiscordTaskRequest:
    kind = "PR" if record.item_kind is GitHubItemKind.PULL_REQUEST else "Issue"
    return DiscordTaskRequest(
        source_kind=DiscordTaskSourceKind.CONTEXT_ACTION,
        guild_id=record.guild_id,
        origin_channel_id=record.channel_id,
        execution_channel_id=thread.id,
        owner_id=actor_id,
        trigger_event_id=trigger_id,
        source_message_id=record.card_message_id,
        prompt=prompt,
        source_label=f"GitHub {kind} #{record.item_number} {action.value.replace('_', ' ')}",
        attachments=StagedDiscordAttachments(paths=(), directory=None),
        origin_context=channel_context(thread),
        intent=_INTENTS[action],
        source_reference_id=record.mirror_id,
        repository_commit_sha=commit_sha,
        task_id=task_id,
    )


def _actor_id(actor: object) -> int:
    actor_id = getattr(actor, "id", None)
    if type(actor_id) is not int or actor_id <= 0:
        raise RuntimeError("The Discord actor identity is invalid.")
    return actor_id


def task_response(prefix: str, task_id: str, thread_id: int | None) -> str:
    location = f" in <#{thread_id}>" if thread_id is not None else ""
    return f"{prefix}{location}. Task `{task_id}`."
