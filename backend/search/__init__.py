"""Finding the chunks that answer a question."""
from .core import Hit, IndexOutOfDate, SearchResult, search

__all__ = ["search", "SearchResult", "Hit", "IndexOutOfDate"]
