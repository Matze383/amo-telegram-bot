from __future__ import annotations

from amo_bot.ai.image_analyze_coreplugin_cp_b5 import (
    FakeImageAnalyzeProvider,
    ImageAnalyzeAdapterConfig,
    ImageAnalyzeProvider,
    ImageAnalyzeProviderError,
    ImageAnalyzeProviderErrorKind,
    ImageAnalyzeProviderResult,
    ImageAnalyzeRequest,
    execute_image_analyze_adapter,
)


class _TimeoutProvider(ImageAnalyzeProvider):
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        raise TimeoutError("provider timeout")


class _ErrorProvider(ImageAnalyzeProvider):
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        raise RuntimeError("boom")


class _ErrorKindProvider(ImageAnalyzeProvider):
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        raise ImageAnalyzeProviderError(kind=ImageAnalyzeProviderErrorKind.ERROR, message="bad")


class _LongOutputProvider(ImageAnalyzeProvider):
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        return ImageAnalyzeProviderResult(summary="x" * 120)


def _req() -> ImageAnalyzeRequest:
    return ImageAnalyzeRequest(image_ref="media://img-1", prompt="describe this image", locale="en")


def test_img_b5_fake_provider_success_with_bounded_output_and_metadata_only_audit() -> None:
    result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="fake", timeout_seconds=1.0, max_output_chars=40),
        provider=FakeImageAnalyzeProvider(),
    )

    assert result.result.value == "deny"
    assert result.reason_code == "not_configured"
    assert result.output_text
    assert len(result.output_text) <= 40
    assert result.audit is not None
    assert result.audit.provider == "fake"
    assert result.audit.reason_code == "not_configured"
    assert result.audit.prompt_length == len(_req().prompt)
    assert "media://img-1" not in result.audit.request_id
    assert "describe this image" not in result.audit.request_id


def test_img_b5_unknown_provider_fails_closed() -> None:
    result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="openai"),
        provider=FakeImageAnalyzeProvider(),
    )

    assert result.result.value == "deny"
    assert result.reason_code == "provider_not_allowed"
    assert result.output_text == ""
    assert result.audit is not None
    assert result.audit.provider == "openai"


def test_img_b5_timeout_and_failure_normalization() -> None:
    timeout_result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="fake", timeout_seconds=1.0),
        provider=_TimeoutProvider(),
    )
    assert timeout_result.reason_code == "provider_timeout"

    failure_result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="fake", timeout_seconds=1.0),
        provider=_ErrorProvider(),
    )
    assert failure_result.reason_code == "provider_error"

    typed_failure_result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="fake", timeout_seconds=1.0),
        provider=_ErrorKindProvider(),
    )
    assert typed_failure_result.reason_code == "provider_error"


def test_img_b5_output_cap_truncation() -> None:
    result = execute_image_analyze_adapter(
        request=_req(),
        config=ImageAnalyzeAdapterConfig(provider_name="fake", max_output_chars=32),
        provider=_LongOutputProvider(),
    )

    assert len(result.output_text) == 32
    assert result.audit is not None
    assert result.audit.truncated is True
    assert result.audit.output_length == 32
