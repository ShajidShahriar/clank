"""Turning a repo into stored chunks and vectors. See docs/indexing-plan.md for the steps."""
from .embed import embed_chunks
from .index import IndexReport, index_project
from .sync import SyncPlan, plan_sync
from .tags import FileTags, classify_file

__all__ = ["embed_chunks", "index_project", "IndexReport", "plan_sync", "SyncPlan", "classify_file", "FileTags"]
