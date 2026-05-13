from __future__ import annotations

from dataclasses import dataclass

from amo_bot.auth.roles import Role
from amo_bot.db.repositories import PluginPolicyOverrideSnapshot
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.service import PluginPolicy


@dataclass(slots=True, frozen=True)
class EffectivePluginPolicy:
    required_roles: list[Role]
    private_mode: str
    groups_mode: str
    topics_mode: str
    allowed_group_ids: set[int]
    allowed_topics: set[tuple[int, int]]


@dataclass(slots=True, frozen=True)
class PolicyEvaluation:
    allowed: bool
    deny_reason: str | None = None


def resolve_effective_policy(*, manifest: PluginManifest, override: PluginPolicyOverrideSnapshot | None) -> EffectivePluginPolicy:
    required_roles = list(manifest.required_roles)
    private_mode = "inherit"
    groups_mode = "inherit"
    topics_mode = "inherit"
    allowed_group_ids: set[int] = set()
    allowed_topics: set[tuple[int, int]] = set()

    if override is not None:
        if override.roles_mode == "override":
            required_roles = list(override.required_roles)
        private_mode = override.private_mode
        groups_mode = override.groups_mode
        topics_mode = override.topics_mode
        allowed_group_ids = set(override.allowed_group_ids)
        allowed_topics = set(override.allowed_topics)

    return EffectivePluginPolicy(
        required_roles=required_roles,
        private_mode=private_mode,
        groups_mode=groups_mode,
        topics_mode=topics_mode,
        allowed_group_ids=allowed_group_ids,
        allowed_topics=allowed_topics,
    )


def evaluate_effective_policy(
    *,
    actor_role: Role,
    effective_policy: EffectivePluginPolicy,
    chat_id: int,
    message_thread_id: int | None,
) -> PolicyEvaluation:
    if not PluginPolicy.is_role_allowed(actor_role=actor_role, plugin_required_roles=effective_policy.required_roles):
        return PolicyEvaluation(allowed=False, deny_reason="role_denied")

    is_private = chat_id > 0

    if is_private:
        if effective_policy.private_mode == "deny":
            return PolicyEvaluation(allowed=False, deny_reason="private_denied")
        return PolicyEvaluation(allowed=True)

    if effective_policy.groups_mode == "deny":
        return PolicyEvaluation(allowed=False, deny_reason="group_denied")
    if effective_policy.groups_mode == "allow" and chat_id not in effective_policy.allowed_group_ids:
        return PolicyEvaluation(allowed=False, deny_reason="group_not_allowed")

    if message_thread_id is not None:
        if effective_policy.topics_mode == "deny":
            return PolicyEvaluation(allowed=False, deny_reason="topic_denied")
        if effective_policy.topics_mode == "allow" and (chat_id, message_thread_id) not in effective_policy.allowed_topics:
            return PolicyEvaluation(allowed=False, deny_reason="topic_not_allowed")

    return PolicyEvaluation(allowed=True)
