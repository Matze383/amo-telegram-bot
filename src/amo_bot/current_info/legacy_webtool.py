from __future__ import annotations

from typing import Any

from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    EvidencePackage,
    SearchResult,
)


class LegacyWebtoolCurrentInfoService:
    """Migration adapter that keeps the old webtool path callable behind the new service shape."""

    def __init__(self, *, dispatcher: Any) -> None:
        self._dispatcher = dispatcher

    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest

        if request.role is None or request.user_id is None or request.chat_id is None:
            return CurrentInfoAnswer(
                status="invalid_request",
                request=request,
                warnings=("legacy_webtool_context_missing",),
            )

        result = self._dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=request.user_id,
                role=request.role,
                chat_id=request.chat_id,
                topic_id=request.topic_id,
                query=request.query,
                locale=request.locale,
                max_results=request.max_results,
                evidence_domain=request.domain_hint,
            )
        )
        if not result.allowed:
            return CurrentInfoAnswer(
                status=result.reason or result.decision or "denied",
                answer_text="",
                request=request,
                warnings=(result.reason or result.decision or "denied",),
                metadata={"legacy_webtool": True, "decision": result.decision},
            )

        search_results = tuple(
            SearchResult(title="", url=url, provider="legacy_webtool", rank=index + 1)
            for index, url in enumerate(result.sources)
        )
        evidence = EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text=result.text,
                    source_url=result.sources[0] if result.sources else "",
                    relevance=0.5,
                    metadata={"result_type": result.result_type},
                ),
            )
            if result.text
            else (),
        )
        return CurrentInfoAnswer(
            status="answered" if result.text else "empty_result",
            answer_text=result.text,
            request=request,
            evidence=evidence,
            sources=tuple(result.sources),
            search_bundle=None,
            metadata={
                "legacy_webtool": True,
                "decision": result.decision,
                "result_type": result.result_type,
                "hosts": tuple(result.hosts),
                "search_result_count": len(search_results),
            },
        )
