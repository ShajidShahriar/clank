# Clank

Local code search for software repositories.

Clank indexes a codebase and answers natural-language questions by retrieving the most relevant code. Embedding, storage and search all run on the local machine through Ollama.

**Status:** in development. The retrieval engine, local API, answer endpoint and desktop integration are complete and tested. The user interface is a prototype and does not show answers yet.

## Overview

Clank splits Python, JavaScript and TypeScript files along their syntax, so each stored piece is a whole function, method or class. Each piece is embedded with the `qwen3-embedding:0.6b` model and stored in SQLite and Chroma. A question is embedded the same way, and the closest pieces are returned, rebuilt into complete passages and trimmed to a token budget.

## Features

- Incremental indexing. Only changed files are processed again.
- Crash-safe indexing. A run interrupted at any point resumes from where it stopped.
- Staleness detection. Passages from files edited after indexing are marked as stale.
- Ranking that demotes test files and changelogs unless the question asks for them.
- A local HTTP API for adding, listing and removing projects, with background indexing, progress reporting, cancellation and a single JSON error format.
- Answer generation through any OpenAI-compatible chat service, hosted or local. Groq's `openai/gpt-oss-120b` is the first supported profile.
- An Electron shell that starts, monitors and stops the backend.

## Security

The backend listens on 127.0.0.1 only. Every request must carry a token that is generated for each launch, and requests with an unexpected Host or Origin header are rejected. The desktop window never sees the token. It reaches the backend through the Electron main process.

## Answer models

The answer endpoint sends the retrieved code excerpts and the question to a chat model and returns the answer together with its sources. The model is chosen by a profile, so adding another provider does not change the rest of the pipeline. Groq is the only built-in profile for now. It reads its key from the `GROQ_API_KEY` environment variable.

Embedding and search always run locally. With a remote model, only the retrieved excerpts (not the whole repository) leave the machine, and only when the request explicitly allows it. A model running on the same computer needs no such permission.

## Evaluation

Retrieval was evaluated on three open-source repositories (this one, Flask and Express) using 20 hand-written questions. Six of the questions were held out and examined once.

| Metric | Result |
|---|---|
| Answer in the top 3 results | 16 of 20 (80%) |
| Answer in the top 10 results | 18 of 20 |
| Answer present in the text sent to the LLM (4000-token budget) | 16 of 20 |
| Median search time | about 35 ms |

The sample is small, so these figures indicate that the approach works and are not a general benchmark. Full tables, design decisions and known failures are in `backend/eval/final/RESULTS.txt`.

## Architecture

```
Electron window  ->  Electron main process  ->  FastAPI backend (127.0.0.1)
(React, prototype)   starts and monitors it     chunk, embed, store, search
                     holds the token            SQLite, Chroma, Ollama
```

## Getting Started

Requirements: Node.js, Python 3.14 or newer, [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com).

```bash
ollama pull qwen3-embedding:0.6b
npm install
uv sync
npm run dev
```

## Testing

```bash
uv run pytest -q          # backend
npm run test:electron     # Electron integration
npx tsc -b                # type check
```

## Roadmap

- Show answers and their sources in the interface.
- Add profiles for local models (Ollama, LM Studio) and a settings screen for the API key.
- Bundle the Python backend into the installer.
