"""Facebook and TikTok analytics ingestion and aggregation.

Planned shape: raw per-post snapshots stored append-only, aggregates derived
on read so a metrics backfill never rewrites history.

TODO(milestone-4): metric snapshot entities + scheduled pull into ``q_integrations``.
"""
