from __future__ import annotations

from typing import Any

from django.conf import settings

DEFAULT_MESSAGE_COMPOSER_SOFT_TEXT_LIMIT_CHARS = 8_000
DEFAULT_MESSAGE_COMPOSER_HARD_TEXT_LIMIT_CHARS = 12_000


def get_message_composer_soft_text_limit_chars() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                "MESSAGE_COMPOSER_SOFT_TEXT_LIMIT_CHARS",
                DEFAULT_MESSAGE_COMPOSER_SOFT_TEXT_LIMIT_CHARS,
            )
        ),
    )


def get_message_composer_hard_text_limit_chars() -> int:
    soft_limit = get_message_composer_soft_text_limit_chars()
    return max(
        soft_limit + 1,
        int(
            getattr(
                settings,
                "MESSAGE_COMPOSER_HARD_TEXT_LIMIT_CHARS",
                DEFAULT_MESSAGE_COMPOSER_HARD_TEXT_LIMIT_CHARS,
            )
        ),
    )


def get_message_composer_template_context() -> dict[str, Any]:
    return {
        "message_composer_soft_text_limit": get_message_composer_soft_text_limit_chars(),
        "message_composer_hard_text_limit": get_message_composer_hard_text_limit_chars(),
    }
