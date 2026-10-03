"""Where a review resource points, and in what order the list reads.

Step 1F.2.3e. The rules for :class:`~meobot.db.models.pr_content_resource.PrContentResource`
that are decisions rather than queries: what a location may be, and which
resource a reviewer should see first.

Reused, not re-decided
----------------------

The security boundary is :mod:`meobot.domain.pr.production`'s, imported rather
than restated: :data:`~meobot.domain.pr.production.ALLOWED_URL_SCHEMES`,
:data:`~meobot.domain.pr.production.UNSAFE_URL_SCHEMES`,
:data:`~meobot.domain.pr.production.DRIVE_HOSTS` and
:data:`~meobot.domain.pr.production.MAX_LOCATION_LENGTH`. Two copies of "which
schemes are safe" is one copy that eventually stops being updated, and this is
not the kind of rule to discover a stale duplicate of.

The ``details['reason']`` vocabulary is shared for the same reason: the panel
already turns ``unsafe_scheme`` and ``not_a_drive_host`` into Vietnamese for
production artifacts, and a resource refused for the same cause should read the
same way.

Why the *string* decides link-or-path here, and the *type* does there
---------------------------------------------------------------------

A :class:`~meobot.domain.pr.models.PrProductionArtifactType` says how a file is
**addressed** - ``NAS_PATH`` versus ``NAS_LINK`` is that distinction and nothing
else - so validation there asks the type.

A :class:`~meobot.domain.pr.models.PrContentResourceType` says what the material
**is**: an image, a brief, a source. Addressing is orthogonal to it - a packshot
may be a Drive file, an ``https`` URL or a path on the NAS, and all three are
``IMAGE``. Making somebody pick the addressing scheme in a second dropdown would
be asking them a question the string they just pasted already answers.

So the shape of the location decides how it is validated. What does **not**
happen is a client deciding the same thing by matching on characters:
:func:`is_link_location` is computed here and travels on the API response, for
the reason :func:`~meobot.domain.pr.production.is_url_artifact` exists - a path
beginning ``//`` looks like a protocol-relative URL to anything reading the
first two bytes.

``DRIVE_FILE`` is the one type that constrains the location, and it constrains it
the way ``DRIVE_LINK`` does: a type promising Google Drive should not hold a
Dropbox URL. A Dropbox URL is not refused - it is ``REFERENCE`` or ``OTHER``.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import PrContentResourceType
from meobot.domain.pr.production import (
    ALLOWED_URL_SCHEMES,
    DRIVE_HOSTS,
    MAX_LOCATION_LENGTH,
    UNSAFE_URL_SCHEMES,
)

#: Long enough to name a thing, short enough for a card. A label is what somebody
#: reads in a list of eight; it is not a description.
MAX_LABEL_LENGTH = 200

#: A note is "dùng packshot số 3", not the brief itself. Bounded so a paste of
#: the whole document lands as a refusal rather than as a row nobody can read.
MAX_NOTE_LENGTH = 2000


def _refuse(reason: str, *, resource_type: PrContentResourceType, location: str) -> None:
    """Raise with a machine-readable reason, mirroring the production refusal.

    Same ``reason`` vocabulary, so a client that already explains
    ``unsafe_scheme`` for a production artifact explains it here too without a
    second table. ``resource_type`` replaces ``artifact_type``; the location is
    echoed back truncated for the reason it is there - somebody needs to see
    which paste was rejected, not two kilobytes of it.
    """
    raise PrValidationError(
        f"Content resource location is not acceptable: {reason}",
        details={
            "field": "location",
            "reason": reason,
            "resource_type": resource_type.value,
            "location": location[:200],
            "allowed_schemes": sorted(ALLOWED_URL_SCHEMES),
        },
    )


def looks_like_path(location: str) -> bool:
    """Is this addressed as a filesystem location rather than a URL?

    An absolute POSIX path or a UNC share. Deliberately structural and
    deliberately narrow: anything else - including a scheme-carrying string, a
    bare host and a relative path - is handled as a URL and refused there with a
    reason that says so, which is a better error than "not a valid path".
    """
    return location.startswith("/") or location.startswith("\\\\")


def is_link_location(location: str) -> bool:
    """Whether a client should render this resource as a link.

    Computed on the server and sent on the response so no client decides it by
    reading the first characters - see the module docstring.
    """
    return not looks_like_path(location)


def normalize_resource_location(resource_type: PrContentResourceType, location: str) -> str:
    """Validate one resource location and return the form to store.

    Normalisation is surrounding whitespace and nothing else, exactly as
    :func:`~meobot.domain.pr.production.normalize_artifact` does it: a resource's
    job is to be the thing somebody actually pointed at, and every "canonical
    form" rewrite has broken a link at some point.

    Raises:
        PrValidationError: Empty, too long, the wrong shape, or carrying a scheme
            this module refuses. ``details['reason']`` names which.
    """
    cleaned = location.strip()
    if not cleaned:
        _refuse("empty", resource_type=resource_type, location=location)
    if len(cleaned) > MAX_LOCATION_LENGTH:
        _refuse("too_long", resource_type=resource_type, location=cleaned)

    if looks_like_path(cleaned):
        if resource_type is PrContentResourceType.DRIVE_FILE:
            # A path is not a Drive file. Refused with the Drive reason rather
            # than a path reason, because the mistake is the type.
            _refuse("not_a_drive_host", resource_type=resource_type, location=cleaned)
        return _validated_path(cleaned, resource_type=resource_type)
    return _validated_url(cleaned, resource_type=resource_type)


def _validated_url(location: str, *, resource_type: PrContentResourceType) -> str:
    """An ``http(s)`` URL with a host, and the right host for ``DRIVE_FILE``."""
    parsed = urlsplit(location)
    scheme = parsed.scheme.lower()
    if not scheme:
        # A bare ``drive.google.com/file/d/…``. Refused rather than repaired:
        # guessing ``https`` stores a URL the person did not write.
        _refuse("missing_scheme", resource_type=resource_type, location=location)
    if scheme not in ALLOWED_URL_SCHEMES:
        # ``javascript:`` and ``data:`` land here, and this is the boundary that
        # keeps them out of an ``href`` the panel renders.
        _refuse(
            "unsafe_scheme" if scheme in UNSAFE_URL_SCHEMES else "unsupported_scheme",
            resource_type=resource_type,
            location=location,
        )
    if not parsed.hostname:
        # ``http:///path`` parses cleanly and addresses nothing.
        _refuse("missing_host", resource_type=resource_type, location=location)
    if (
        resource_type is PrContentResourceType.DRIVE_FILE
        and (parsed.hostname or "").lower() not in DRIVE_HOSTS
    ):
        _refuse("not_a_drive_host", resource_type=resource_type, location=location)
    return location


def _validated_path(location: str, *, resource_type: PrContentResourceType) -> str:
    """An absolute NAS location: a POSIX path or a UNC share.

    The same rule :func:`~meobot.domain.pr.production._validated_path` applies,
    and for the same reason: a relative path means nothing without knowing which
    directory somebody was standing in, and nothing records that.

    MeoBot never opens any of these. They are stored so a person can copy them.
    """
    if location.startswith("\\\\"):
        # UNC: ``\\host\share\...`` needs at least a host and a share.
        parts = [part for part in location[2:].split("\\") if part]
        if len(parts) < 2:
            _refuse("incomplete_unc_path", resource_type=resource_type, location=location)
    return location


def normalize_label(label: str) -> str:
    """A human name for the resource. Required, and never the URL by default.

    The rule this enforces is small and the reason is not: a list of eight rows
    each titled ``https://drive.google.com/file/d/1a2B…`` is a list nobody can
    scan, and the person adding it is the only one who knows it is the packshot.

    Raises:
        PrValidationError: Blank after stripping, or longer than
            :data:`MAX_LABEL_LENGTH`.
    """
    cleaned = label.strip()
    if not cleaned:
        raise PrValidationError(
            "Content resource label is required",
            details={"field": "label", "reason": "empty"},
        )
    if len(cleaned) > MAX_LABEL_LENGTH:
        raise PrValidationError(
            "Content resource label is too long",
            details={"field": "label", "reason": "too_long", "max_length": MAX_LABEL_LENGTH},
        )
    return cleaned


def normalize_note(note: str | None) -> str | None:
    """The optional "what to look at" line. ``None`` and blank are the same thing.

    Plain text, stored as typed. Nothing here parses it, and nothing renders it
    as markup - see the panel, which puts it in a text node.

    Raises:
        PrValidationError: Longer than :data:`MAX_NOTE_LENGTH`.
    """
    if note is None:
        return None
    cleaned = note.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise PrValidationError(
            "Content resource note is too long",
            details={"field": "note", "reason": "too_long", "max_length": MAX_NOTE_LENGTH},
        )
    return cleaned


#: Resource types in declaration order, for a stable secondary sort. The enum is
#: the authority; this is only its ordering, as ``_EDITABLE_IN_ORDER`` is for
#: stages.
RESOURCE_TYPE_ORDER: tuple[PrContentResourceType, ...] = tuple(PrContentResourceType)


__all__: list[str] = [
    "MAX_LABEL_LENGTH",
    "MAX_NOTE_LENGTH",
    "RESOURCE_TYPE_ORDER",
    "is_link_location",
    "looks_like_path",
    "normalize_label",
    "normalize_note",
    "normalize_resource_location",
]
