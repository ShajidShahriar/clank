"""Finding the chunks that answer a question."""
from .core import Hit, IndexOutOfDate, SearchResult, search
from .stitch import Passage, expand

__all__ = ["search", "SearchResult", "Hit", "IndexOutOfDate", "expand", "Passage"]
