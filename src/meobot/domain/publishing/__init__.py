"""Publishing approved videos to Facebook Pages and TikTok.

Hard rule: publishing may only start from a video in
``VideoStatus.APPROVED_FOR_PUBLISH`` (or later). The policy engine enforces
this via ``VIDEO_STATE_GUARDS``; the publisher must re-check before the call.

TODO(milestone-4): publish jobs with idempotency keys, retry and rollback notes.
"""
