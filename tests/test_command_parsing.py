from amo_bot.telegram.update_parser import TelegramChat, TelegramMessage, TelegramUser


def _msg(text: str) -> TelegramMessage:
    return TelegramMessage(
        message_id=1,
        from_user=TelegramUser(id=100, is_bot=False, first_name="A", username="alice"),
        chat=TelegramChat(id=200, type="private", title=None, username=None),
        text=text,
    )


def test_parse_ping() -> None:
    cmd = _msg("/ping").parse_command(bot_username="MyBot")
    assert cmd is not None
    assert cmd.name == "ping"
    assert cmd.argument is None


def test_parse_ping_with_argument() -> None:
    cmd = _msg("/ping hello").parse_command(bot_username="MyBot")
    assert cmd is not None
    assert cmd.name == "ping"
    assert cmd.argument == "hello"


def test_parse_ping_with_botname() -> None:
    cmd = _msg("/ping@BotName arg").parse_command(bot_username="BotName")
    assert cmd is not None
    assert cmd.name == "ping"
    assert cmd.target_bot == "BotName"
    assert cmd.argument == "arg"


def test_parse_wrong_botname_returns_none() -> None:
    cmd = _msg("/ping@OtherBot arg").parse_command(bot_username="BotName")
    assert cmd is None


def test_parse_suffix_without_configured_botname_returns_none() -> None:
    cmd = _msg("/ping@OtherBot arg").parse_command(bot_username=None)
    assert cmd is None


def test_parse_normal_message_returns_none() -> None:
    cmd = _msg("hello world").parse_command(bot_username="BotName")
    assert cmd is None
