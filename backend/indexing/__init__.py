"""Turning a repo into stored chunks and vectors. See docs/indexing-plan.md for the steps."""
from .embed import embed_chunks
from .sync import SyncPlan, plan_sync

__all__ = ["embed_chunks", "plan_sync", "SyncPlan"]
