"""The answer model. `LLMClient` is the interface; `OpenAICompatibleClient` is the real one (Groq, Ollama, LM Studio, OpenRouter, OpenAI), `FakeLLM` is for tests."""
from .base import Completion, LLMClient, LLMStream, StreamDone, StreamEvent, TextPiece, ThinkingPiece, host_is_local
from .errors import (LLMAuthError, LLMBadResponse, LLMContextTooLong, LLMError, LLMModelNotFound, LLMNotConfigured, LLMRateLimited, LLMTimeout,
                     LLMUnavailable)
from .fake import FakeLLM
from .openai_compat import OpenAICompatibleClient

__all__ = ["LLMClient", "LLMStream", "StreamEvent", "StreamDone", "TextPiece", "ThinkingPiece", "Completion", "FakeLLM", "OpenAICompatibleClient", "host_is_local", "LLMError", "LLMNotConfigured", "LLMAuthError",
           "LLMModelNotFound", "LLMContextTooLong", "LLMRateLimited", "LLMUnavailable", "LLMTimeout", "LLMBadResponse"]
