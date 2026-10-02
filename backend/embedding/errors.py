"""Errors an embedder can raise. All of them are loud on purpose: a quiet failure here means a chunk
that silently never reaches search."""


class EmbeddingError(Exception):
    """Base class."""


class OllamaUnavailable(EmbeddingError):
    """Ollama did not answer (not running, still loading after every retry, connection dropped)."""


class ModelNotFound(EmbeddingError):
    """Ollama is running but does not have the model."""


class BadResponse(EmbeddingError):
    """Ollama answered, but not with one vector of the right length per text."""
