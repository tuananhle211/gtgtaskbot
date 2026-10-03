"""Safe Telegram message rendering.

Every user-facing send goes through :func:`answer`. That exists because of a
real outage: ``/help`` was sent with ``parse_mode="Markdown"`` and its body
contained seven ``_`` characters (``/script_types``, ``/add_sheet``, ...). An
odd number of underscores is an unterminated italic entity, Telegram answered
``400 Can't find end of Italic entity``, aiogram raised, and the user saw
nothing at all.

The rules this module enforces:

* **One parse mode.** HTML everywhere. It has exactly four special characters,
  they are escapable, and ``_`` - which appears in every command name MeoBot
  has - is not one of them.
* **Escape dynamic text.** Sheet names, folder names and LLM output are never
  interpolated raw into markup.
* **Split, do not truncate.** A message over Telegram's limit is split on line
  boundaries; a truncated instruction is a different instruction.
* **Never fail silently.** If Telegram still rejects the markup, the same text
  is re-sent as plain text and the reason is logged. A formatting mistake
  degrades the message, it does not delete it.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from html import escape as _html_escape
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from meobot.core.logging import get_logger

logger = get_logger(__name__)

#: Telegram rejects a ``sendMessage`` body longer than this.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

#: Leaves room for the "(1/3)" style continuation markers we never add, and for
#: multi-byte accents Telegram counts differently from ``len()`` in edge cases.
SAFE_CHUNK_LENGTH = 3800

#: The single parse mode MeoBot uses. See the module docstring.
PARSE_MODE = "HTML"


def escape(text: object) -> str:
    """Escape dynamic text for :data:`PARSE_MODE`.

    Vietnamese diacritics pass through untouched - only ``&``, ``<`` and ``>``
    are entities in Telegram's HTML subset.
    """
    return _html_escape(str(text), quote=False)


def bold(text: object) -> str:
    """Bold, with the content escaped."""
    return f"<b>{escape(text)}</b>"


def code(text: object) -> str:
    """Inline code, with the content escaped."""
    return f"<code>{escape(text)}</code>"


def link(url: str, label: str) -> str:
    """Anchor with both parts escaped."""
    return f'<a href="{_html_escape(url, quote=True)}">{escape(label)}</a>'


def split_message(text: str, *, limit: int = SAFE_CHUNK_LENGTH) -> list[str]:
    """Split ``text`` into chunks Telegram will accept.

    Splits on paragraph breaks first, then on line breaks, and only falls back
    to a hard character cut for a single line longer than ``limit`` (which in
    practice means somebody pasted a wall of text).

    Returns:
        At least one chunk; ``[""]`` becomes ``[]`` so callers never send an
        empty message.
    """
    if not text.strip():
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            # One physical line longer than a whole message: cut it.
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return [chunk.rstrip("\n") or chunk for chunk in chunks if chunk.strip()]


async def answer(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = PARSE_MODE,
    disable_web_page_preview: bool = True,
) -> None:
    """Reply to ``message``, splitting and degrading safely.

    The keyboard is attached to the last chunk only, so buttons stay next to
    the end of the text.

    Never raises for a formatting problem: a rejected parse mode is retried as
    plain text, and a send that fails for any other reason is logged with its
    Telegram reason rather than surfacing a traceback to the user.
    """
    chunks = split_message(text)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await _send_one(
            message,
            chunk,
            reply_markup=markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )


async def _send_one(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None,
    parse_mode: str | None,
    disable_web_page_preview: bool,
) -> None:
    try:
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
        return
    except TelegramBadRequest as exc:
        if parse_mode is None:
            logger.warning(
                "telegram_send_rejected",
                extra={"reason": str(exc)[:200], "length": len(text)},
            )
            return
        # Almost always an entity error. Log the reason (Telegram's message is
        # about markup, never about content) and resend without markup so the
        # user still gets the information.
        logger.warning(
            "telegram_parse_mode_rejected",
            extra={"reason": str(exc)[:200], "parse_mode": parse_mode, "length": len(text)},
        )
    except Exception:
        logger.exception("telegram_send_failed", extra={"length": len(text)})
        return

    try:
        await message.answer(
            _strip_markup(text),
            reply_markup=reply_markup,
            parse_mode=None,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception:
        logger.exception("telegram_plain_send_failed", extra={"length": len(text)})


async def answer_callback(
    query: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = PARSE_MODE,
) -> None:
    """Send a new message into the chat a callback button came from."""
    if isinstance(query.message, Message):
        await answer(query.message, text, reply_markup=reply_markup, parse_mode=parse_mode)


async def edit_callback(query: CallbackQuery, text: str, *, parse_mode: str | None = PARSE_MODE):  # type: ignore[no-untyped-def]
    """Replace the message a button belongs to, falling back to a new message."""
    if isinstance(query.message, Message):
        with contextlib.suppress(Exception):
            await query.message.edit_text(text, parse_mode=parse_mode)
            return
    await answer_callback(query, text, parse_mode=parse_mode)


def _strip_markup(text: str) -> str:
    """Best-effort plain-text version of an HTML-formatted message.

    Only the tags this module produces are removed; anything else is left
    alone, because an unmatched ``<`` in user content is exactly the case that
    got us here.
    """
    plain = text
    for tag in ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>", "<pre>", "</pre>"):
        plain = plain.replace(tag, "")
    return plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def bullet_list(items: Iterable[str], *, bullet: str = "•") -> str:
    """Render pre-escaped ``items`` as a bullet list."""
    return "\n".join(f"{bullet} {item}" for item in items)


# --- Model output -----------------------------------------------------------
# A language model writes Markdown whether or not it was asked to. Until
# 0.6.0a2 every generated reply went through ``escape`` and nothing else, which
# is correct for ``<``, ``>`` and ``&`` and has no opinion at all about ``**``.
# So a model that wrote ``**Thời gian:**`` produced a message with the asterisks
# visible, and users read the markup.
#
# The rule here: **escape first, then re-introduce only the tags we chose.**
# Escaping first is what makes this safe - by the time any tag is added, every
# ``<`` in the model's output is already ``&lt;``, so no generated string can
# close a tag we opened or open one we did not.
#
# Only three constructs are supported: bold, italic and bullets. A wider subset
# would mean more ways for unbalanced markup to reach Telegram, and Telegram
# rejecting a message is how a reply disappears entirely.

#: ``**bold**`` and ``__bold__``. Non-greedy, single-line: an unmatched marker
#: therefore fails to match and is stripped rather than swallowing the message.
_MD_BOLD = re.compile(r"\*\*(?!\s)([^\n*]+?)\*\*|__(?!\s)([^\n_]+?)__")

#: ``*italic*`` and ``_italic_``, after bold has already been consumed.
#: ``_`` is required to stand alone so ``/script_types`` survives untouched -
#: the command names in this bot are full of underscores, and mangling one is
#: how the original ``/help`` outage started.
_MD_ITALIC = re.compile(
    r"(?<![\w*])\*(?!\s)([^\n*]+?)\*(?![\w*])"
    r"|(?<![\w_])_(?!\s)([^\n_]+?)_(?![\w_])"
)

#: A leading ``-`` or ``*`` bullet, which Telegram has no markup for.
_MD_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)

#: Anything left over once the supported constructs are converted. These are
#: markers the model produced that we deliberately do not render; showing them
#: raw is exactly the bug, so they are removed.
_MD_LEFTOVER = re.compile(r"\*{1,3}")

#: ``### Heading`` - dropped to plain text; Telegram has no heading.
_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)


def render_assistant_text(text: object) -> str:
    """Render model output as safe Telegram HTML.

    Handles the small Markdown subset a model actually produces and removes the
    rest, so no user ever sees a raw ``**``. Every dynamic character is escaped
    *before* any tag is introduced, and the result is checked for balance -
    an unbalanced tag would make Telegram reject the whole message, and a reply
    that disappears is worse than one that lost its bolding.

    Returns:
        HTML safe for :data:`PARSE_MODE`, with balanced ``<b>``/``<i>`` tags.
    """
    escaped = escape(str(text))
    converted = _MD_HEADING.sub("", escaped)
    converted = _MD_BULLET.sub("• ", converted)
    converted = _MD_BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", converted)
    converted = _MD_ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", converted)
    converted = _MD_LEFTOVER.sub("", converted)

    if not _tags_balanced(converted):
        # Something pathological. Drop the markup rather than risk Telegram
        # refusing the message: the words matter, the bolding does not.
        logger.info("assistant_markup_unbalanced", extra={"length": len(converted)})
        return _MD_LEFTOVER.sub("", escaped)
    return converted


def _tags_balanced(html: str) -> bool:
    """True when every tag this module opens is closed, in order."""
    stack: list[str] = []
    for match in re.finditer(r"</?([bi])>", html):
        if match.group(0).startswith("</"):
            if not stack or stack.pop() != match.group(1):
                return False
        else:
            stack.append(match.group(1))
    return not stack


async def answer_assistant(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Reply with model-generated text, rendered through :func:`render_assistant_text`."""
    await answer(message, render_assistant_text(text), reply_markup=reply_markup)


async def send_typing(message: Message, *, enabled: bool = True) -> None:
    """Show "typing..." once, before a slow call starts.

    A failure to send the chat action is swallowed: the indicator is a nicety,
    and losing it must never lose the answer.
    """
    if not enabled:
        return
    with contextlib.suppress(Exception):
        await message.bot.send_chat_action(message.chat.id, "typing")  # type: ignore[union-attr]


@contextlib.asynccontextmanager
async def typing(message: Message, *, enabled: bool = True) -> AsyncIterator[None]:
    """Context-manager form of :func:`send_typing`, for callers that wrap a call."""
    await send_typing(message, enabled=enabled)
    yield


def keyboard(rows: Sequence[Sequence[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build an inline keyboard from ``[(label, callback_data), ...]`` rows."""
    from aiogram.types import InlineKeyboardButton

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def format_kv(pairs: Sequence[tuple[str, Any]]) -> str:
    """Render ``label: value`` lines with both sides escaped."""
    return "\n".join(f"{escape(label)}: {escape(value)}" for label, value in pairs)
