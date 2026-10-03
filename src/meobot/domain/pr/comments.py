"""What a comment on a piece of content is allowed to be.

Step 1F.2.3g. Two rules, and both of them are about restraint.

Plain text, and one level
-------------------------

A comment is **discussion**: *"hook đoạn đầu hơi dài, cắt còn 3 giây nhé"*. It is
not an approval, not an audit event, not a content version and not a task, and
nothing in the workflow reads one. So it carries a body and nothing else - no
markup, no mentions, no attachments, no reactions - and
:data:`MAX_COMMENT_DEPTH` is 1 because a thread that can nest arbitrarily needs a
display that can draw a tree, and the operational question this feature answers
(*"what did the team say about this cut"*) has never needed one.

The body is stored exactly as typed, trimmed at the ends. Nothing here parses it
and nothing renders it as markup - the panel puts it in a text node, which is the
same decision :func:`~meobot.domain.pr.resources.normalize_note` documents for a
resource note and for the same reason: a comment box is the one place in this
module where somebody can type ``<script>`` on purpose.

Why the length limit is 2000
-----------------------------

The same number as a resource note and for the same reason: it is long enough
that nobody writing a real paragraph hits it, and short enough that a comment is
never a document. Somebody who needs a document has a brief, which is a
:class:`~meobot.db.models.pr_content_resource.PrContentResource` with a link.
"""

from __future__ import annotations

from meobot.domain.pr.errors import PrValidationError

#: The longest a comment may be. See the module docstring - deliberately the
#: same as :data:`~meobot.domain.pr.resources.MAX_NOTE_LENGTH`.
MAX_COMMENT_LENGTH = 2000

#: How deep a thread goes. ``1`` means a root and its replies, and nothing
#: below that. A reply naming a reply is refused rather than silently
#: re-parented, because quietly moving somebody's answer under a different
#: question changes what they said.
MAX_COMMENT_DEPTH = 1

#: How many root comments one page carries by default. A content item with a
#: hundred comments is a real thing; a response with a hundred is not.
DEFAULT_COMMENT_PAGE = 50

#: The ceiling a client may ask for, matching the PR API's list cap.
MAX_COMMENT_PAGE = 200


def normalize_comment_body(body: str) -> str:
    """Validate one comment body and return the form to store.

    Surrounding whitespace is stripped and the inside is left exactly as typed -
    the line breaks somebody used are part of what they wrote.

    Raises:
        PrValidationError: Empty or whitespace-only, or longer than
            :data:`MAX_COMMENT_LENGTH`. ``details['reason']`` names which.
    """
    cleaned = body.strip()
    if not cleaned:
        raise PrValidationError(
            "Comment body is required",
            details={"field": "body", "reason": "empty"},
        )
    if len(cleaned) > MAX_COMMENT_LENGTH:
        raise PrValidationError(
            "Comment body is too long",
            details={
                "field": "body",
                "reason": "too_long",
                "max_length": MAX_COMMENT_LENGTH,
            },
        )
    return cleaned


__all__: list[str] = [
    "DEFAULT_COMMENT_PAGE",
    "MAX_COMMENT_DEPTH",
    "MAX_COMMENT_LENGTH",
    "MAX_COMMENT_PAGE",
    "normalize_comment_body",
]
