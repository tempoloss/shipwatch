"""Send a message to Telegram.

Telegram is where notifications are actually seen, so time-sensitive things go
here. Duplicates are harmless, so this sink does no deduplication.
"""

from __future__ import annotations

from ..core import http_post_json


def send(token: str, chat_id: str, text: str) -> None:
    http_post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": text,
         "parse_mode": "Markdown", "disable_web_page_preview": True},
    )
