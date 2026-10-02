"""Turning text into vectors. `Embedder` is the interface; `OllamaEmbedder` is the real one, `FakeEmbedder` is for tests."""
from .base import Embedder, Vector
from .errors import BadResponse, EmbeddingError, EmbeddingTooLong, ModelNotFound, OllamaUnavailable
from .fake import FakeEmbedder
from .ollama import OllamaEmbedder

__all__ = ["Embedder", "Vector", "FakeEmbedder", "OllamaEmbedder",
           "EmbeddingError", "EmbeddingTooLong", "OllamaUnavailable", "ModelNotFound", "BadResponse"]
