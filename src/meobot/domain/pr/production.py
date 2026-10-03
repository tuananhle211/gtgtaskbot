"""Where a finished production file lives, and what counts as saying so.

Step 1F.2.3. ``PRODUCTION -> INTERNAL_REVIEW`` is the one transition in this
module that a person cannot honestly drive by pressing a button: an internal
reviewer is being asked to watch *a cut*, and a submission with no cut attached
sends them to look for a file nobody named. So the transition carries a
reference, and this module is what a reference has to be.

It validates. It does not fetch
--------------------------------

Nothing here opens a URL, resolves a host, touches the NAS or asks whether a file
exists, and nothing downstream does either. Two reasons, and the second is the
one that matters:

* a validator that made a network call would fail when a website was slow, and
  would make submitting a file depend on the file's server being up;
* **MeoBot has no NAS credentials and must not acquire any for this.** The
  reference is a pointer for a person to follow with their own access. Checking
  it would mean holding the team's storage credentials to answer a question the
  reviewer answers by clicking.

So this is a syntax check with a safety rule, and the docstrings say which is
which.

The safety rule
---------------

A stored reference is rendered as a link. That makes the accepted scheme set a
security boundary rather than a formatting preference:

* ``http`` and ``https`` only, and an ``https`` URL must have a host;
* ``javascript:`` is script execution wearing a link's clothes - the classic
  stored-XSS payload, and it survives every escaping layer that only escapes
  HTML;
* ``data:`` smuggles a whole document, including a script, into what looks like
  an address;
* ``file:`` addresses the *reader's* machine, not the team's storage, so it is
  both a misleading record and an invitation to probe local paths.

They are refused here, in the domain, rather than in a form: the browser is one
client of four, and a rule enforced where the button is is a rule the Telegram
tool and any script do not have.

NAS references are not pretend URLs
-----------------------------------

The team's storage is reachable two ways and they are genuinely different:
File Station serves ``https`` links, and the same file has a path on the volume
that people paste to each other. A path is not a URL, so
:attr:`~meobot.domain.pr.models.PrProductionArtifactType.NAS_PATH` is its own
type with its own rule - absolute POSIX (``/volume1/...``) or a UNC share
(``\\\\nas\\pr\\...``) - and a client renders it as text to copy rather than as a
link to click. Wrapping it in ``file://`` to reuse the URL branch would have
produced a reference that looks clickable and opens nothing.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from meobot.domain.pr.errors import PrValidationError
from meobot.domain.pr.models import (
    PrProductionArtifactType,
    PrProductionHandoff,
    PrWorkflowStage,
)


def handoff_state(
    workflow_stage: PrWorkflowStage, producer_user_id: object | None
) -> PrProductionHandoff | None:
    """Where this piece stands in the production handoff, or ``None``.

    Step 1F.2.3b. Two columns in, one reading out, and no storage of its own -
    see :class:`~meobot.domain.pr.models.PrProductionHandoff` on why this is
    derived rather than a stage.

    ``None`` for everything before ``APPROVED`` and after ``INTERNAL_REVIEW``:
    a piece at ``SCRIPTING`` has no handoff state, and saying so with a value
    would invite a client to render "chưa sản xuất" on an idea.
    """
    if workflow_stage is PrWorkflowStage.APPROVED:
        return (
            PrProductionHandoff.READY_FOR_PRODUCTION
            if producer_user_id is not None
            else PrProductionHandoff.WAITING_FOR_PRODUCER
        )
    if workflow_stage is PrWorkflowStage.PRODUCTION:
        return PrProductionHandoff.IN_PRODUCTION
    if workflow_stage is PrWorkflowStage.INTERNAL_REVIEW:
        return PrProductionHandoff.IN_INTERNAL_REVIEW
    return None


#: The stages at which a producer may be named. ``APPROVED`` is the important
#: one and is what Step 1F.2.3b corrected: the handoff happens *before*
#: production starts, so waiting for ``PRODUCTION`` to assign somebody meant
#: nobody could be assigned until after the work had begun.
HANDOFF_STAGES: frozenset[PrWorkflowStage] = frozenset(
    {PrWorkflowStage.APPROVED, PrWorkflowStage.PRODUCTION}
)


#: The only schemes a stored reference may carry. See the module docstring - this
#: is a security boundary, not a formatting preference.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: Schemes named individually so a refusal can say which one it recognised, and
#: so the test suite has something to enumerate. Anything outside
#: :data:`ALLOWED_URL_SCHEMES` is refused whether or not it appears here.
UNSAFE_URL_SCHEMES: frozenset[str] = frozenset({"javascript", "data", "file", "vbscript", "blob"})

#: Hosts that make a reference a Google Drive reference. Checked so that
#: ``DRIVE_LINK`` means what it says; a Dropbox URL is not refused, it is simply
#: ``EXTERNAL_LINK``.
DRIVE_HOSTS: frozenset[str] = frozenset({"drive.google.com", "docs.google.com"})

#: The URL-shaped artifact types. ``NAS_PATH`` is deliberately absent.
URL_ARTIFACT_TYPES: frozenset[PrProductionArtifactType] = frozenset(
    {
        PrProductionArtifactType.DRIVE_LINK,
        PrProductionArtifactType.NAS_LINK,
        PrProductionArtifactType.EXTERNAL_LINK,
    }
)

#: Long enough for a signed Drive URL with query parameters, bounded so a
#: pasted accident cannot become a megabyte in a text column.
MAX_LOCATION_LENGTH = 2000


def _refuse(reason: str, *, artifact_type: PrProductionArtifactType, location: str) -> None:
    """Raise the one error this module raises, with a machine-readable reason.

    ``reason`` is the field a client branches on to write its own sentence - the
    PR services speak structural English by design, and the Vietnamese lives in
    ``tools/pr_errors.py`` and in the panel's label table. The location is echoed
    back **truncated**: a person needs to see which paste was rejected, and an
    error body is not the place to reproduce two kilobytes of URL.
    """
    raise PrValidationError(
        f"Production artifact reference is not acceptable: {reason}",
        details={
            "field": "location",
            "reason": reason,
            "artifact_type": artifact_type.value,
            "location": location[:200],
            "allowed_schemes": sorted(ALLOWED_URL_SCHEMES),
        },
    )


def normalize_artifact(artifact_type: PrProductionArtifactType, location: str) -> str:
    """Validate one reference and return the form to store.

    Normalisation is deliberately minimal - surrounding whitespace, which is what
    a paste from a chat window carries. Nothing lower-cases a path, strips a
    query string or rewrites a Drive URL into its "canonical" form: every one of
    those has broken somebody's link at some point, and the reference's whole job
    is to be the thing the producer actually pointed at.

    Raises:
        PrValidationError: Empty, too long, the wrong shape for its type, or
            carrying a scheme this module refuses. ``details['reason']`` names
            which, so a client can say so in its own words.
    """
    cleaned = location.strip()
    if not cleaned:
        _refuse("empty", artifact_type=artifact_type, location=location)
    if len(cleaned) > MAX_LOCATION_LENGTH:
        _refuse("too_long", artifact_type=artifact_type, location=cleaned)

    if artifact_type is PrProductionArtifactType.NAS_PATH:
        return _validated_path(cleaned, artifact_type=artifact_type)
    return _validated_url(cleaned, artifact_type=artifact_type)


def _validated_url(location: str, *, artifact_type: PrProductionArtifactType) -> str:
    """An ``http(s)`` URL with a host, and the right host for its type."""
    parsed = urlsplit(location)
    scheme = parsed.scheme.lower()
    if not scheme:
        # A bare ``drive.google.com/file/d/…`` is the common paste. Refused
        # rather than repaired: guessing ``https`` would store a URL the person
        # did not write, and the fix is one word they can type.
        _refuse("missing_scheme", artifact_type=artifact_type, location=location)
    if scheme not in ALLOWED_URL_SCHEMES:
        _refuse(
            "unsafe_scheme" if scheme in UNSAFE_URL_SCHEMES else "unsupported_scheme",
            artifact_type=artifact_type,
            location=location,
        )
    if not parsed.hostname:
        # ``http:///path`` parses cleanly and addresses nothing.
        _refuse("missing_host", artifact_type=artifact_type, location=location)
    if (
        artifact_type is PrProductionArtifactType.DRIVE_LINK
        and (parsed.hostname or "").lower() not in DRIVE_HOSTS
    ):
        _refuse("not_a_drive_host", artifact_type=artifact_type, location=location)
    return location


#: A drive letter and a separator - ``M:\`` or ``m:/``. Matched **before**
#: anything asks about a scheme, because ``urlsplit`` reads ``M:`` as one: a
#: single letter is a syntactically valid scheme, so a mapped-drive path would
#: otherwise be refused as "that is a URL of some kind".
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def looks_like_storage_path(location: str) -> bool:
    """Is this addressed as a stored file rather than as a web resource?

    Step 1F.2.3f.2, and the one place that question is answered. Four shapes are
    a path - see the module docstring - and the rule for the fourth is what makes
    this decidable rather than a guess: **a string that names no scheme is a
    path.**

    That is deliberately blunt, and the alternative was worse. Telling
    ``shared/project/file.mp4`` apart from a pasted ``drive.google.com/file/d/1``
    means guessing whether the first segment looks like a hostname - which
    refuses ``video-final.mp4`` and accepts ``my.folder/clip.mp4`` for the same
    reason. That heuristic is wrong in both directions on values people really
    type.

    So a scheme-less string is stored as a path and **rendered as text**, and
    somebody who meant to paste a URL sees at once that it is not a link and
    fixes it. Nothing unsafe follows: only ``http`` and ``https`` are ever made
    clickable - see :func:`~meobot.domain.pr.assets.is_link_location` - and a
    string naming any other scheme is refused outright.
    """
    stripped = location.strip()
    if stripped.startswith("/") or stripped.startswith("\\\\"):
        return True
    if _WINDOWS_DRIVE.match(stripped):
        return True
    return not urlsplit(stripped).scheme


def _validated_path(location: str, *, artifact_type: PrProductionArtifactType) -> str:
    """A stored file location: POSIX, UNC, mapped drive, or relative.

    Widened by Step 1F.2.3f.2 - see the module docstring for the four shapes and
    for why "absolute or nothing" was making the stored data worse rather than
    safer.

    A string carrying a scheme is still refused: somebody has chosen the wrong
    type, and silently accepting ``https://…`` here would store a URL that
    nothing renders as a link.
    """
    if _WINDOWS_DRIVE.match(location):
        # Checked first: ``urlsplit`` reads ``M:`` as a scheme.
        return location
    if "://" in location or urlsplit(location).scheme:
        _refuse("not_a_path", artifact_type=artifact_type, location=location)
    if location.startswith("\\\\"):
        # UNC: ``\\host\share\...`` needs at least a host and a share. Kept
        # strict, because a lone ``\\host`` addresses a machine rather than a
        # file and storing one would send the next person hunting through a
        # whole NAS.
        parts = [part for part in location[2:].split("\\") if part]
        if len(parts) < 2:
            _refuse("incomplete_unc_path", artifact_type=artifact_type, location=location)
        return location
    # Absolute POSIX, or a location the team resolves against a root they
    # already share. Both are stored exactly as typed.
    return location


def is_url_artifact(artifact_type: PrProductionArtifactType) -> bool:
    """Whether a client should render this reference as a link.

    Asked by the panel so it does not decide from the string's shape - a
    ``NAS_PATH`` beginning with ``//`` would look like a protocol-relative URL to
    anything matching on characters.
    """
    return artifact_type in URL_ARTIFACT_TYPES


__all__: list[str] = [
    "ALLOWED_URL_SCHEMES",
    "DRIVE_HOSTS",
    "HANDOFF_STAGES",
    "MAX_LOCATION_LENGTH",
    "UNSAFE_URL_SCHEMES",
    "URL_ARTIFACT_TYPES",
    "handoff_state",
    "is_url_artifact",
    "looks_like_storage_path",
    "normalize_artifact",
]
