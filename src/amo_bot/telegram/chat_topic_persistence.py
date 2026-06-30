from __future__ import annotations

import logging
import json
import re

from sqlalchemy.orm import sessionmaker

from amo_bot.ai.claims import extract_claims
from amo_bot.ai.memory_c2_service import MemoryC2Service, MemoryScope
from amo_bot.db.context_memory_vector import ContextMemoryVectorRepository
from amo_bot.db.models import GROUP_CHAT_TYPES, AuditEvent, Claim
from amo_bot.db.repositories import ClaimRepository, ChatSeenUserRepository, ChatTopicRepository, TopicAgentMemoryRepository, UserMemoryProfileRepository, UserRoleRepository
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage, TelegramUser


logger = logging.getLogger(__name__)


def _extract_coarse_profile_candidate(text: str) -> dict[str, object]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return {}

    lower = normalized.casefold()
    candidate: dict[str, object] = {}

    if any(marker in lower for marker in ("sprich deutsch", "antworte auf deutsch", "antwort auf deutsch", "rede deutsch")):
        candidate["language"] = "de"
    elif any(marker in lower for marker in ("speak english", "answer in english", "reply in english")):
        candidate["language"] = "en"

    if any(marker in lower for marker in ("halte dich kurz", "antworte mir lieber kurz", "bitte kurz", "keep it short", "short answers")):
        candidate["verbosity"] = "low"
        candidate["communication_style"] = "brief"
    elif any(marker in lower for marker in ("ausführlich", "detailliert", "detailed answer", "explain in detail")):
        candidate["verbosity"] = "high"
        candidate["communication_style"] = "detailed"

    if any(marker in lower for marker in ("sei direkt", "direkt mit mir", "be direct")):
        candidate["tone_preference"] = "direct"
    elif any(marker in lower for marker in ("freundlich", "sei freundlich", "friendly tone")):
        candidate["tone_preference"] = "friendly"
    elif any(marker in lower for marker in ("formal", "förmlich", "formell")):
        candidate["tone_preference"] = "formal"

    if any(marker in lower for marker in ("stichpunkte", "bullet points", "als liste")):
        candidate["format_preference"] = "bullet_points"
    elif any(marker in lower for marker in ("schritt für schritt", "step by step")):
        candidate["format_preference"] = "step_by_step"

    role_patterns = (
        (r"\bich bin (?:hier )?(tester|entwickler|developer|admin|moderator)\b", 1),
        (r"\bi am (?:a |an )?(tester|developer|admin|moderator)\b", 1),
    )
    for pattern, group_idx in role_patterns:
        match = re.search(pattern, lower)
        if match:
            role = match.group(group_idx)
            role_map = {"entwickler": "developer"}
            candidate["context_role"] = role_map.get(role, role)
            break

    return candidate


class ChatTopicPersistenceService:
    def __init__(
        self,
        session_factory: sessionmaker,
        send_private_message=None,
        owner_notifier: OwnerNotifier | None = None,
        send_group_markup=None,
        send_group_text=None,
        bot_username: str | None = None,
        context_vector_repository: ContextMemoryVectorRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._send_private_message = send_private_message
        self._owner_notifier = owner_notifier
        self._send_group_markup = send_group_markup
        self._send_group_text = send_group_text
        self._bot_username = bot_username
        self._context_vector_repository = context_vector_repository

    async def persist_bot_peer_recent_message(self, message: TelegramMessage) -> None:
        with self._session_factory() as session:
            repo = ChatTopicRepository(session)
            repo.upsert_chat(
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
                username=message.chat.username,
            )
            if message.message_thread_id is not None:
                repo.upsert_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    telegram_topic_name=message.telegram_topic_name,
                )

            text = (message.text or "").strip()
            if text and not text.startswith("/"):
                self._persist_recent_message(
                    session=session,
                    chat_type=message.chat.type,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    private_user_id=message.from_user.id,
                    message_id=message.message_id,
                    author=message.from_user,
                    text=text,
                    source="bot",
                )

            session.commit()

    async def persist_message(self, message: TelegramMessage) -> None:
        with self._session_factory() as session:
            user_repo = UserRoleRepository(session)
            existing_user = user_repo.get_user_by_telegram_id(message.from_user.id)
            user = user_repo.upsert_discovered_user(
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name or None,
                last_name=message.from_user.last_name,
            )

            repo = ChatTopicRepository(session)
            repo.upsert_chat(
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
                username=message.chat.username,
            )
            if message.chat.type in GROUP_CHAT_TYPES:
                ChatSeenUserRepository(session).mark_seen(
                    chat_id=message.chat.id,
                    telegram_user_id=message.from_user.id,
                )
            if message.message_thread_id is not None:
                repo.upsert_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    telegram_topic_name=message.telegram_topic_name,
                )

            text = (message.text or "").strip()
            if text and not text.startswith("/"):
                self._persist_recent_message(
                    session=session,
                    chat_type=message.chat.type,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    private_user_id=message.from_user.id,
                    message_id=message.message_id,
                    author=message.from_user,
                    text=text,
                    source="bot" if message.from_user.is_bot else "user",
                )
                if not message.from_user.is_bot:
                    self._maybe_update_user_profile_from_message(
                        session=session,
                        chat_type=message.chat.type,
                        chat_id=message.chat.id,
                        message_thread_id=message.message_thread_id,
                        user_id=message.from_user.id,
                        text=text,
                    )

            reply_to = message.reply_to_message
            reply_text = (message.reply_to_message_text or "").strip()
            if reply_to is not None and reply_text and not reply_text.startswith("/"):
                self._persist_recent_message(
                    session=session,
                    chat_type=message.chat.type,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    private_user_id=message.from_user.id if message.chat.type == "private" else None,
                    message_id=reply_to.message_id,
                    author=reply_to.from_user,
                    text=reply_text,
                    source="bot" if (reply_to.from_user and reply_to.from_user.is_bot) else "user",
                    skip_existing=True,
                )

            if existing_user is None and self._owner_notifier is not None:
                await self._owner_notifier.notify_new_user_discovered(user=user, message=message)

            session.commit()

    async def persist_edited_message(self, message: TelegramMessage) -> bool:
        with self._session_factory() as session:
            repo = ChatTopicRepository(session)
            repo.upsert_chat(
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
                username=message.chat.username,
            )
            if message.message_thread_id is not None:
                repo.upsert_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    telegram_topic_name=message.telegram_topic_name,
                )

            scope = self._recent_scope_for_message(
                chat_type=message.chat.type,
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                private_user_id=message.chat.id if message.from_user.is_bot else message.from_user.id,
            )
            if scope is None:
                session.commit()
                logger.info(
                    "edited Telegram message ignored: unsupported chat_type=%s chat_id=%s message_id=%s",
                    message.chat.type,
                    message.chat.id,
                    message.message_id,
                )
                return False

            scope_type, scope_chat_id, topic_id, user_id = scope
            source = "bot" if message.from_user.is_bot else "user"
            text = (message.text or "").strip()
            updated = TopicAgentMemoryRepository(
                session,
                vector_repository=self._context_vector_repository,
            ).update_recent_by_telegram_message_id(
                scope_type=scope_type,
                chat_id=scope_chat_id,
                topic_id=topic_id,
                user_id=user_id,
                telegram_message_id=message.message_id,
                message_text=text,
                telegram_author_user_id=message.from_user.id,
                telegram_author_username=message.from_user.username,
                telegram_author_is_bot=message.from_user.is_bot,
                source=source,
            )
            if updated is None:
                session.commit()
                logger.info(
                    "edited Telegram message has no stored original: chat_id=%s topic_id=%s message_id=%s",
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                )
                return False

            self._delete_unverified_claims_for_message(
                session=session,
                scope_type=scope_type,
                chat_id=scope_chat_id,
                topic_id=topic_id,
                user_id=user_id,
                source_message_id=message.message_id,
                source=source,
            )
            if text:
                self._persist_claims_from_text(
                    session=session,
                    scope_type=scope_type,
                    chat_id=scope_chat_id,
                    topic_id=topic_id,
                    user_id=user_id,
                    source_message_id=message.message_id,
                    source=source,
                    text=text,
                )
            session.commit()
            return True

    def _maybe_update_user_profile_from_message(
        self,
        *,
        session,
        chat_type: str,
        chat_id: int,
        message_thread_id: int | None,
        user_id: int,
        text: str,
    ) -> None:
        candidate = _extract_coarse_profile_candidate(text)
        if not candidate:
            return

        if chat_type in GROUP_CHAT_TYPES:
            if message_thread_id is not None:
                scope = MemoryScope(scope_type="topic", chat_id=chat_id, topic_id=message_thread_id, user_id=user_id)
            else:
                scope = MemoryScope(scope_type="group_chat", chat_id=chat_id, user_id=user_id)
        elif chat_type == "private":
            scope = MemoryScope(scope_type="private_user", user_id=user_id)
        else:
            return

        profile_repo = UserMemoryProfileRepository(session)
        service = MemoryC2Service(
            repository=TopicAgentMemoryRepository(session, vector_repository=self._context_vector_repository),
            profile_repository=profile_repo,
        )
        result = service.apply_profile_candidate(scope=scope, candidate=candidate)
        if not result.accepted_keys and not result.rejected_keys:
            return

        session.add(
            AuditEvent(
                actor_telegram_user_id=user_id,
                event_type="user_profile_auto_update",
                payload_json=json.dumps(
                    {
                        "scope_type": scope.scope_type,
                        "chat_id": scope.chat_id,
                        "topic_id": scope.topic_id,
                        "user_id": user_id,
                        "accepted_keys": list(result.accepted_keys),
                        "rejected_keys": list(result.rejected_keys),
                    }
                ),
            )
        )

    def _persist_recent_message(
        self,
        *,
        session,
        chat_type: str,
        chat_id: int,
        message_thread_id: int | None,
        private_user_id: int | None,
        message_id: int,
        author: TelegramUser | None,
        text: str,
        source: str,
        skip_existing: bool = False,
    ) -> None:
        scope = self._recent_scope_for_message(
            chat_type=chat_type,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            private_user_id=private_user_id,
        )

        if scope is None:
            return

        scope_type, scope_chat_id, topic_id, user_id = scope
        repo = TopicAgentMemoryRepository(session, vector_repository=self._context_vector_repository)
        if skip_existing and repo.get_recent_by_telegram_message_id(
            scope_type=scope_type,
            chat_id=scope_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            telegram_message_id=message_id,
        ) is not None:
            return

        repo.add_message(
            scope_type=scope_type,
            chat_id=scope_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            message_text=text,
            telegram_message_id=message_id,
            telegram_author_user_id=author.id if author is not None else None,
            telegram_author_username=author.username if author is not None else None,
            telegram_author_is_bot=bool(author and author.is_bot),
            source=source,
        )
        self._persist_claims_from_text(
            session=session,
            scope_type=scope_type,
            chat_id=scope_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            source_message_id=message_id,
            source=source,
            text=text,
        )

    @staticmethod
    def _recent_scope_for_message(
        *,
        chat_type: str,
        chat_id: int,
        message_thread_id: int | None,
        private_user_id: int | None,
    ) -> tuple[str, int | None, int | None, int | None] | None:
        if chat_type in GROUP_CHAT_TYPES:
            if message_thread_id is not None:
                return ("topic", chat_id, message_thread_id, None)
            return ("group_chat", chat_id, None, None)
        if chat_type == "private" and private_user_id is not None:
            return ("private_user", None, None, private_user_id)
        return None

    @staticmethod
    def _delete_unverified_claims_for_message(
        *,
        session,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
        source_message_id: int,
        source: str,
    ) -> None:
        source_type = "bot_claim" if source == "bot" else "user_claim"
        session.query(Claim).filter(
            Claim.scope_type == scope_type,
            Claim.chat_id == chat_id,
            Claim.topic_id == topic_id,
            Claim.user_id == user_id,
            Claim.source_type == source_type,
            Claim.source_message_id == source_message_id,
            Claim.verification_status == "unverified",
        ).delete(synchronize_session=False)

    @staticmethod
    def _persist_claims_from_text(
        *,
        session,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
        source_message_id: int,
        source: str,
        text: str,
    ) -> None:
        source_type = "bot_claim" if source == "bot" else "user_claim"
        claim_repo = ClaimRepository(session)
        for claim in extract_claims(text):
            claim_repo.create_claim(
                text=claim.text,
                normalized_subject=claim.normalized_subject,
                source_type=source_type,
                source_message_id=source_message_id,
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                confidence=claim.confidence,
                auto_commit=False,
            )

    async def persist_bot_sent_message(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        message_id: int,
        text: str,
        bot_username: str | None = None,
    ) -> None:
        content = (text or "").strip()
        if not content or content.startswith("/"):
            return
        with self._session_factory() as session:
            self._persist_recent_message(
                session=session,
                chat_type="supergroup" if chat_id < 0 else "private",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                private_user_id=chat_id if chat_id > 0 else None,
                message_id=message_id,
                author=TelegramUser(
                    id=0,
                    is_bot=True,
                    first_name="Bot",
                    username=(bot_username or self._bot_username),
                ),
                text=content,
                source="bot",
                skip_existing=True,
            )
            session.commit()
