"""
Aiogram middleware that gates *every* incoming update against the
configured single-user TELEGRAM_CHAT_ID.

The per-handler is_authorized() checks already cover the message
handlers, but callback_query handlers in search / folders / trash were
not all guarded individually. Putting the check at the dispatcher
level guarantees that any update (message, callback_query, edited
message, inline query, …) from a non-authorized user is dropped before
it reaches any router.
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import is_authorized

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        # data["event_from_user"] is set by aiogram for any update where
        # a from_user can be extracted (messages, callback queries,
        # inline queries, edited messages, etc.).
        user = data.get("event_from_user")
        if user is not None and not is_authorized(user.id):
            logger.warning(
                "Blocked update from unauthorized user id=%s username=%s",
                user.id, getattr(user, "username", None),
            )
            return  # silently drop — don't even acknowledge
        return await handler(event, data)
