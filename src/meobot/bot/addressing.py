"""Is this message aimed at MeoBot, and what did it actually say?

Extracted from the conversation handler because the access gate now needs the
same answers *before* any handler runs: whether a message is addressed to
MeoBot decides whether a stranger's mention opens an approval request, and
whether a member's message costs them a chat slot.

There is exactly one implementation of each rule, so the gate and the handler
can never disagree about what "addressed to MeoBot" means.
"""

from __future__ import annotations

import re

from aiogram.types import Message

#: ``@MeoBot`` / ``@meobot_dev_bot`` anywhere in the text.
MENTION_PATTERN = re.compile(r"(?<!\w)@[A-Za-z0-9_]{4,32}\b")

#: Chat types where a mention is not required, whatever the setting says.
PRIVATE_CHAT_TYPES: frozenset[str] = frozenset({"private"})


def strip_bot_mention(text: str, bot_username: str | None) -> str:
    """Remove ``@botusername`` from a message.

    In a group the same request arrives as "@MeoBot đồng bộ các Sheet"; in a
    private chat as "đồng bộ các Sheet". Both must mean the same thing, so the
    mention is removed before anything looks at the text.

    Only *this* bot's username is removed when it is known. When it is not, a
    *leading* mention is dropped as well - in a group that is how a message is
    addressed to somebody - but a mention in the middle of a sentence is left
    alone, because there it is content: "@linh viết kịch bản này" names a
    person, not a recipient.
    """
    if not text:
        return text
    cleaned = text
    if bot_username:
        cleaned = re.sub(rf"(?<!\w)@{re.escape(bot_username)}\b", " ", cleaned, flags=re.IGNORECASE)
    else:
        cleaned = re.sub(r"^\s*@[A-Za-z0-9_]{4,32}\b", " ", cleaned)
    return " ".join(cleaned.split())


def bot_username_of(message: Message) -> str | None:
    """This bot's ``@username``, if aiogram already knows it.

    Read from aiogram's cached ``getMe`` result rather than fetched: resolving
    a username must not add an API round-trip to every incoming message, and a
    missing one is handled by :func:`strip_bot_mention`.
    """
    if message.bot is None:
        return None
    username = getattr(getattr(message.bot, "_me", None), "username", None)
    return str(username) if username else None


def is_addressed_to_bot(message: Message, *, require_mention: bool) -> bool:
    """Whether MeoBot should answer this message at all.

    In a private chat, always: the user opened a conversation with the bot, and
    demanding a mention there is the single most common way to make a bot look
    broken. In a group, only when mentioned or replied to - otherwise MeoBot
    would answer every message the team sends each other, and every stranger's
    passing remark would become an approval request for the owner.
    """
    if message.chat.type in PRIVATE_CHAT_TYPES:
        return True
    if not require_mention:
        return True

    username = bot_username_of(message)
    text = message.text or ""
    if username and re.search(rf"(?<!\w)@{re.escape(username)}\b", text, flags=re.IGNORECASE):
        return True
    reply_to = message.reply_to_message
    return reply_to is not None and reply_to.from_user is not None and reply_to.from_user.is_bot


def replied_to_user(message: Message) -> tuple[int | None, str | None, str | None, bool]:
    """``(telegram_id, username, display_name, is_bot)`` of the replied-to author.

    The only reliable way to point an administrative command at a person:
    Telegram supplies the numeric id, so nothing has to be guessed from a
    display name that two people can share and anybody can change.

    Returns ``(None, ...)`` when the message is not a reply, or when the reply
    is to a channel post or an anonymous admin - neither of which has a user id
    to act on.
    """
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None, None, None, False
    author = reply.from_user
    return author.id, author.username, author.full_name, bool(author.is_bot)
