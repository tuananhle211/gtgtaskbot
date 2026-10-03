"""Meta (Facebook Pages) integration.

Milestone 1: protocols and fakes only. Nothing here talks to graph.facebook.com.
"""

from meobot.integrations.meta.metrics import (
    FacebookMetricsClient,
    FakeFacebookMetricsClient,
    PostMetrics,
)
from meobot.integrations.meta.publisher import (
    FacebookPublisher,
    FakeFacebookPublisher,
    PublishRequest,
    PublishResult,
)

__all__ = [
    "FacebookMetricsClient",
    "FacebookPublisher",
    "FakeFacebookMetricsClient",
    "FakeFacebookPublisher",
    "PostMetrics",
    "PublishRequest",
    "PublishResult",
]
