"""Turning text into vectors. `Embedder` is the interface; `FakeEmbedder` is for tests."""
from .base import Embedder, Vector
from .fake import FakeEmbedder

__all__ = ["Embedder", "Vector", "FakeEmbedder"]
