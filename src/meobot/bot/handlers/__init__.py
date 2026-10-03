"""Telegram handlers grouped by kind.

Include order matters: every command router comes first, the free-text
conversation router last, so a slash command is never swallowed by the
natural-language handler.

The ``guest`` router is included **before** every other one and matches only
updates the access gate authorised as a Guest. That ordering is what keeps a
Guest out of the tool pipeline: their message is consumed there and never
offered to a handler that expects an ``Actor``.

None of the command handlers carry a state filter, which in aiogram 3 means
they match in *any* FSM state. That is deliberate: ``/help`` and
``/cancel_flow`` have to work in the middle of ``/add_sheet`` or
``/create_script_sheet``, not only from a clean slate.
"""

from meobot.bot.handlers.access import router as access_router
from meobot.bot.handlers.chat import router as chat_router
from meobot.bot.handlers.commands import router as commands_router
from meobot.bot.handlers.conversation import router as conversation_router
from meobot.bot.handlers.dispatch import router as dispatch_router
from meobot.bot.handlers.drive import router as drive_router
from meobot.bot.handlers.group_admin import router as group_admin_router
from meobot.bot.handlers.guest import router as guest_router
from meobot.bot.handlers.hr import router as hr_router
from meobot.bot.handlers.invites import router as invites_router
from meobot.bot.handlers.member import router as member_router
from meobot.bot.handlers.notifications import router as notifications_router
from meobot.bot.handlers.people import router as people_router
from meobot.bot.handlers.read_receipts import router as read_receipts_router
from meobot.bot.handlers.reminders import router as reminders_router
from meobot.bot.handlers.scripts import router as scripts_router
from meobot.bot.handlers.sheets import router as sheets_router
from meobot.bot.handlers.web import router as web_router

__all__ = [
    "access_router",
    "chat_router",
    "commands_router",
    "conversation_router",
    "dispatch_router",
    "drive_router",
    "group_admin_router",
    "guest_router",
    "hr_router",
    "invites_router",
    "member_router",
    "notifications_router",
    "people_router",
    "read_receipts_router",
    "reminders_router",
    "scripts_router",
    "sheets_router",
    "web_router",
]
