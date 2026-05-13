from amo_bot.telegram.update_parser import parse_update


def _mk_base_update() -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": -1001, "type": "supergroup", "title": "G"},
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "text": "hello",
        },
    }


def test_parse_reply_to_bot_sets_flag_true() -> None:
    raw = _mk_base_update()
    raw["message"]["reply_to_message"] = {  # type: ignore[index]
        "message_id": 9,
        "from": {"id": 99, "is_bot": True, "first_name": "Bot"},
        "chat": {"id": -1001, "type": "supergroup", "title": "G"},
        "text": "bot msg",
    }

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert update.message.reply_to_is_bot is True


def test_parse_reply_to_other_sets_flag_false() -> None:
    raw = _mk_base_update()
    raw["message"]["reply_to_message"] = {  # type: ignore[index]
        "message_id": 9,
        "from": {"id": 77, "is_bot": False, "first_name": "Other"},
        "chat": {"id": -1001, "type": "supergroup", "title": "G"},
        "text": "human msg",
    }

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert update.message.reply_to_is_bot is False


def test_parse_without_reply_sets_flag_false() -> None:
    update = parse_update(_mk_base_update())
    assert update is not None
    assert update.message is not None
    assert update.message.reply_to_is_bot is False
