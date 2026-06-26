from __future__ import annotations

from datetime import UTC, datetime

from amo_bot.telegram.webtool_news_corroboration import assess_news_corroboration
from amo_bot.telegram.webtool_research_orchestrator import _format_news_corroboration_response


_NOW = datetime(2026, 6, 11, tzinfo=UTC)


def test_same_claim_from_two_sources_is_corroborated():
    claim = (
        "2026-06-11 Acme announced regulator approval for the Berlin battery plant after the agency completed "
        "its environmental review, and the company said construction will begin this summer with local officials present."
    )

    result = assess_news_corroboration(
        (
            ("webscraping", "news-one.example", claim),
            ("webscraping", "news-two.example", claim),
        ),
        now=_NOW,
    )

    assert result.status == "corroborated"
    assert result.supporting_hosts == ("news-one.example", "news-two.example")


def test_two_hosts_repeating_same_weak_snippet_fail_closed():
    snippet = "2026-06-11 Acme announced regulator approval for the Berlin battery plant."

    result = assess_news_corroboration(
        (
            ("webscraping", "snippet-one.example", snippet),
            ("webscraping", "snippet-two.example", snippet),
        ),
        now=_NOW,
    )

    assert result.status == "weak_repeated_snippet"
    assert result.supporting_hosts == ("snippet-one.example", "snippet-two.example")


def test_stale_source_is_not_counted_as_current_corroboration():
    stale = (
        "2020-01-10 Acme announced regulator approval for the Berlin battery plant after the agency completed "
        "its environmental review, and the company said construction will begin this summer with local officials present."
    )

    result = assess_news_corroboration(
        (
            ("webscraping", "old-one.example", stale),
            ("webscraping", "old-two.example", stale),
        ),
        now=_NOW,
    )

    assert result.status == "stale_sources"
    assert result.stale_hosts == ("old-one.example", "old-two.example")


def test_conflicting_claims_are_reported_before_corroboration():
    positive = (
        "2026-06-11 Acme announced regulator approval for the Berlin battery plant after the agency completed "
        "its environmental review, and the company said construction will begin this summer with local officials present."
    )
    negative = (
        "2026-06-11 Acme has not announced regulator approval for the Berlin battery plant after the agency completed "
        "its environmental review, and the company said construction will not begin this summer with local officials present."
    )

    result = assess_news_corroboration(
        (
            ("webscraping", "positive.example", positive),
            ("webscraping", "negative.example", negative),
        ),
        now=_NOW,
    )

    assert result.status == "conflicting_claims"
    assert result.conflict_hosts == ("negative.example", "positive.example")


def test_primary_source_is_preferred_when_same_claim_is_supported():
    claim = (
        "2026-06-11 Official statement Acme announced regulator approval for the Berlin battery plant after the agency "
        "completed its environmental review, and the company said construction will begin this summer with local officials present."
    )

    result = assess_news_corroboration(
        (
            ("webscraping", "agency.gov", claim),
            ("webscraping", "wire.example", claim),
            ("webscraping", "blog.example", "2026-06-11 A different short market reaction item was published."),
        ),
        now=_NOW,
    )

    assert result.status == "corroborated"
    assert result.primary_hosts == ("agency.gov",)
    assert result.supporting_hosts == ("agency.gov", "wire.example")


def test_single_trusted_government_primary_source_can_corroborate_when_checked_and_current():
    claim = (
        "2026-06-11 The national agency announced regulator approval for the Berlin battery plant after the "
        "environmental review concluded, and construction will begin this summer with local officials present."
    )

    result = assess_news_corroboration((("webscraping", "press.example.gov", claim),), now=_NOW)

    assert result.status == "corroborated"
    assert result.supporting_hosts == ("press.example.gov",)
    assert result.primary_hosts == ("press.example.gov",)


def test_institutional_primary_source_rules_cover_real_official_domains():
    claim = (
        "2026-06-11 The ministry announced regulator approval for the Berlin battery plant after the environmental "
        "review concluded, and construction will begin this summer with local officials present."
    )

    for host in (
        "cdc.gov",
        "www.cdc.gov",
        "sub.cdc.gov",
        "gov.uk",
        "www.gov.uk",
        "service.gov.uk",
        "who.int",
        "www.who.int",
        "sub.who.int",
        "europa.eu",
        "commission.europa.eu",
        "army.mil",
        "sub.army.mil",
        "itu.int",
        "sub.itu.int",
        "bund.de",
        "www.bund.de",
    ):
        result = assess_news_corroboration((("webscraping", host, claim),), now=_NOW)

        assert result.status == "corroborated", host
        assert result.primary_hosts == (host.removeprefix("www."),), host


def test_official_and_press_lookalike_hosts_do_not_get_single_source_bypass():
    claim = (
        "2026-06-11 Acme announced regulator approval for the Berlin battery plant after the agency completed "
        "its environmental review, and the company said construction will begin this summer with local officials present."
    )

    for host in (
        "official-news.example",
        "press-release.example",
        "wordpress.example",
        "news.gov.example",
        "example.gov.com",
        "who.int.example",
        "europa.eu.example",
    ):
        result = assess_news_corroboration((("webscraping", host, claim),), now=_NOW)

        assert result.status == "no_corroborated_claim", host
        assert result.primary_hosts == (), host


def test_stale_or_weak_trusted_primary_source_still_fails_closed():
    stale = (
        "2020-01-10 The national agency announced regulator approval for the Berlin battery plant after the "
        "environmental review concluded, and construction will begin this summer with local officials present."
    )
    weak = "2026-06-11 The national agency announced regulator approval for the Berlin battery plant."

    stale_result = assess_news_corroboration((("webscraping", "agency.gov", stale),), now=_NOW)
    weak_result = assess_news_corroboration((("webscraping", "agency.gov", weak),), now=_NOW)

    assert stale_result.status == "stale_sources"
    assert stale_result.primary_hosts == ()
    assert weak_result.status == "no_corroborated_claim"
    assert weak_result.primary_hosts == ()


def test_fail_closed_response_names_uncertainty_without_raw_dump():
    raw_text = (
        "Acme announced regulator approval for the Berlin battery plant. "
        "This raw paragraph should not be dumped into the user response."
    )

    response = _format_news_corroboration_response(
        request_text="latest news about Acme",
        extracts=(("webscraping", "single.example", raw_text),),
        locale="en",
    )

    assert "cannot reliably confirm" in response
    assert "claim level" in response
    assert "Source/status:" in response
    assert "raw paragraph" not in response


def test_fail_closed_response_mentions_conflict_status_without_raw_tool_output():
    response = _format_news_corroboration_response(
        request_text="latest news about Acme",
        extracts=(
            (
                "webscraping",
                "positive.example",
                "Acme announced regulator approval for the Berlin battery plant after the agency completed its review.",
            ),
            (
                "webscraping",
                "negative.example",
                "Acme has not announced regulator approval for the Berlin battery plant after the agency completed its review.",
            ),
        ),
        locale="de",
    )

    assert "Aussage-Ebene" in response
    assert "conflicting checked claims" in response
    assert "Acme has not announced" not in response
