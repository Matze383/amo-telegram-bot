from amo_bot.telegram.update_parser import parse_update


def _mk_base_update() -> dict[str, object]:
    return {
        "update_id": 100,
        "message": {
            "message_id": 10,
            "chat": {"id": -1001, "type": "supergroup", "title": "G"},
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "text": "hello",
        },
    }


def test_parse_text_only_has_no_attachments() -> None:
    update = parse_update(_mk_base_update())
    assert update is not None
    assert update.message is not None
    assert update.message.text == "hello"
    assert update.message.attachments == ()


def test_parse_photo_picks_largest_variant_and_safe_fields() -> None:
    raw = _mk_base_update()
    raw["message"]["photo"] = [  # type: ignore[index]
        {
            "file_id": "small-id",
            "file_unique_id": "small-uid",
            "width": 90,
            "height": 90,
            "file_size": 123,
        },
        {
            "file_id": "large-id",
            "file_unique_id": "large-uid",
            "width": 800,
            "height": 600,
            "file_size": 4567,
        },
    ]

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert len(update.message.attachments) == 1

    attachment = update.message.attachments[0]
    assert attachment.source_kind == "photo"
    assert attachment.type_hint == "image"
    assert attachment.file_id == "large-id"
    assert attachment.file_unique_id == "large-uid"
    assert attachment.width == 800
    assert attachment.height == 600
    assert attachment.size == 4567


def test_parse_image_document_creates_attachment() -> None:
    raw = _mk_base_update()
    raw["message"]["document"] = {  # type: ignore[index]
        "file_id": "doc-file-id",
        "file_unique_id": "doc-file-uid",
        "mime_type": "image/png",
        "width": 512,
        "height": 256,
        "file_size": 999,
    }

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert len(update.message.attachments) == 1

    attachment = update.message.attachments[0]
    assert attachment.source_kind == "document"
    assert attachment.type_hint == "image_document"
    assert attachment.file_id == "doc-file-id"
    assert attachment.file_unique_id == "doc-file-uid"
    assert attachment.width == 512
    assert attachment.height == 256
    assert attachment.size == 999


def test_parse_non_image_document_is_ignored() -> None:
    raw = _mk_base_update()
    raw["message"]["document"] = {  # type: ignore[index]
        "file_id": "doc-file-id",
        "file_unique_id": "doc-file-uid",
        "mime_type": "application/pdf",
        "file_size": 999,
    }

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert update.message.attachments == ()


def test_parse_malformed_media_is_safe_noop() -> None:
    raw = _mk_base_update()
    raw["message"]["photo"] = [  # type: ignore[index]
        {"file_id": "", "width": "nope", "height": None},
        "invalid",
    ]
    raw["message"]["document"] = {"mime_type": 123, "file_id": None}  # type: ignore[index]

    update = parse_update(raw)
    assert update is not None
    assert update.message is not None
    assert update.message.attachments == ()
