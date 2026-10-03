"""Where a derivative, a destination link and a publication point.

Step 1F.2.3f, widened by 1F.2.3f.2. Three kinds of thing carry an address, and
this module is the whole of what each address may be. It is a **security
boundary wearing a validation function**, and the reason it is one module
rather than three checks written
beside three services is that the schemes a link may carry is exactly the kind of
rule that must not exist in more than one place.

Reused, not re-decided
----------------------

Nothing here invents a scheme list, a length limit or a refusal vocabulary.
:mod:`meobot.domain.pr.production` owns all three and has since Step 1F.2.3, and
every function below delegates into :func:`~meobot.domain.pr.production.normalize_artifact`
so that a ``javascript:`` URL is refused by the same code path whichever of the
five kinds of location somebody pastes it into. That also means the panel's
existing Vietnamese for ``unsafe_scheme``, ``missing_scheme``, ``missing_host``,
``not_a_path`` and ``too_long`` already covers these fields on the day they ship.

The three rules, and why they differ
------------------------------------

They are not the same rule, and flattening them would be wrong in both
directions:

* an **asset location** - where a produced file lives - is addressed exactly as
  a production submission is: an ``http(s)`` URL *or* a stored path. Which one
  is decided by the **shape of the string**, so a producer pasting
  ``/volume1/PR/2026/cut-25s.mp4`` does not first have to answer a dropdown
  about addressing schemes.

  Step 1F.2.3f.2 widened what counts as a path, because the original "absolute
  POSIX or UNC" rule refused two shapes the team actually uses - a mapped drive
  (``M:\\XAY KENH\\...``) and a location relative to a shared root
  (``shared/project/file.mp4``). The grammar lives in
  :func:`~meobot.domain.pr.production.looks_like_storage_path`, next to the
  scheme rules, so "what may an asset location be" has one answer;
* a **destination link** is a commercial page a customer is sent to - a landing
  page, a booking page, a store listing. It is always a URL, never a path: a NAS
  path in that field is not a destination anybody can be sent to, and accepting
  one would put a dead string in the field whose entire job is to be clickable;
* a **publication URL** is the live public post. Same rule as a destination, and
  a separate function because the two answer different questions and a future
  change to one should not silently change the other.

What still does not happen
--------------------------

**Nothing is fetched.** No request is made to any of these addresses - not to
check a link is alive, not to read an Open Graph title, not to confirm a post
exists. Step 1F.2.3e wrote down why for resources and it has not changed: a
validator that made a network call would fail when somebody else's website was
slow, and would make recording your own work depend on a third party's uptime.
It is also what keeps this feature away from SSRF and away from quietly fetching
a client's private document.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from meobot.domain.pr.models import PrProductionArtifactType
from meobot.domain.pr.production import (
    ALLOWED_URL_SCHEMES,
    looks_like_storage_path,
    normalize_artifact,
)

#: How long a human label may be, matching
#: :data:`~meobot.domain.pr.resources.MAX_LABEL_LENGTH` because it is the same
#: kind of field on the same kind of screen.
MAX_LABEL_LENGTH = 200


def normalize_asset_location(location: str) -> str:
    """Validate where a produced file lives and return the form to store.

    **The one rule for an asset location**, Step 1F.2.3f.2, and the answer to
    "where does the cutdown live". A URL or a stored path, decided by the shape
    of what was pasted:

    * it **names a scheme** - then it is a web address and must be ``http`` or
      ``https`` with a host. ``javascript:``, ``data:``, ``vbscript:``,
      ``file:`` and everything else are refused here, which is what stops a
      stored location ever becoming an executable link;
    * it **names no scheme** - then it is a path, and the four shapes
      :func:`~meobot.domain.pr.production.looks_like_storage_path` recognises are
      all stored verbatim.

    The work is delegated to
    :func:`~meobot.domain.pr.production.normalize_artifact`, which is the same
    function an original production submission goes through: a derivative is a
    produced file, and there is no reason for it to be addressable differently
    from the master it was cut from. What this adds is the shape inference, for
    the callers that have no ``artifact_type`` column to ask.

    Raises:
        PrValidationError: Empty, too long, an incomplete UNC share, or carrying
            a scheme this product refuses. ``details['reason']`` names which,
            from the vocabulary the panel already words.
    """
    return normalize_artifact(_addressing(location), location)


#: The previous name for :func:`normalize_asset_location`. Kept because Step
#: 1F.2.3f's derivative service and its tests read it, and because the two are
#: genuinely the same operation - a derivative's location *is* an asset location.
normalize_derivative_location = normalize_asset_location


def is_link_location(location: str) -> bool:
    """Whether a client should render this location as a clickable link.

    Positively "names a scheme we allow", and deliberately not "is not a path".
    Step 1F.2.3f.2 widened what counts as a path to include relative locations,
    which made the negative form answer *"link"* for anything scheme-carrying -
    including the schemes this product exists to refuse. Nothing unsafe can
    reach a stored row, but a display rule that would linkify ``javascript:`` if
    one ever did is not a rule worth keeping.

    Computed on the server so no client decides it by reading the first
    characters: a path beginning ``//`` looks like a protocol-relative URL to
    anything matching on the string, and ``M:\\...`` looks like a scheme.
    """
    return urlsplit(location.strip()).scheme.lower() in ALLOWED_URL_SCHEMES


def normalize_destination_url(url: str) -> str:
    """Validate a product or landing-page URL and return the form to store.

    ``http`` or ``https``, with a host. **Never a path**: a destination is
    somewhere a customer is sent, and a NAS path is not that. Refused with
    ``not_a_path``'s mirror - the ``EXTERNAL_LINK`` branch's ``missing_scheme``
    - rather than accepted as a string nothing can open.

    Raises:
        PrValidationError: Empty, too long, missing a scheme or a host, or
            carrying a refused scheme.
    """
    return normalize_artifact(PrProductionArtifactType.EXTERNAL_LINK, url)


def normalize_publication_url(url: str) -> str:
    """Validate the live public post URL and return the form to store.

    The same rule as :func:`normalize_destination_url`, and deliberately its own
    function rather than an alias: one is where a customer is sent and the other
    is where the post is, they are edited on different screens by different
    people, and a future change to either should have to be written twice on
    purpose rather than propagate by accident.

    Raises:
        PrValidationError: As :func:`normalize_destination_url`.
    """
    return normalize_artifact(PrProductionArtifactType.EXTERNAL_LINK, url)


def _addressing(location: str) -> PrProductionArtifactType:
    """Which artifact rule this string should be judged by.

    ``NAS_PATH`` for something path-shaped and ``EXTERNAL_LINK`` for everything
    else - and **no new vocabulary**: Step 1F.2.3f.2 deliberately reuses
    :class:`~meobot.domain.pr.models.PrProductionArtifactType` rather than adding
    a second way to say the same thing, because a production submission already
    stores exactly this distinction in a column.

    ``EXTERNAL_LINK`` rather than ``DRIVE_LINK`` even for a Drive URL: the Drive
    branch *constrains the host*, which is right when somebody has chosen the
    type "Google Drive" and wrong when nobody has chosen anything - a Dropbox
    link to a cutdown is a perfectly good cutdown.
    """
    return (
        PrProductionArtifactType.NAS_PATH
        if looks_like_storage_path(location)
        else PrProductionArtifactType.EXTERNAL_LINK
    )


__all__: list[str] = [
    "MAX_LABEL_LENGTH",
    "is_link_location",
    "normalize_asset_location",
    "normalize_derivative_location",
    "normalize_destination_url",
    "normalize_publication_url",
]
