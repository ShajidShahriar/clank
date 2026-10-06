# Clank

Local code search for software repositories.

Clank indexes a codebase and answers natural-language questions by retrieving the most relevant code. Embedding, storage and search all run on the local machine through Ollama.

**Status:** in development. The retrieval engine, local API, answer endpoint and desktop integration are complete and tested. The desktop interface is an early version: it can add a project folder, index it with live progress, and show answers with their sources. Conversations are not saved yet.

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
- A desktop interface to add and index projects, ask questions and read answers with the code they were based on, including a switch that controls whether code may be sent to a remote model.

## Security

The backend listens on 127.0.0.1 only. Every request must carry a token that is generated for each launch, and requests with an unexpected Host or Origin header are rejected. The desktop window never sees the token. It reaches the backend through the Electron main process.

## Answer models

The answer endpoint sends the retrieved code excerpts and the question to a chat model and returns the answer together with its sources. Any service that speaks the OpenAI chat protocol works. Presets are included for Groq, Ollama, LM Studio and a custom address (OpenRouter, OpenAI, vLLM or your own server). The provider, address, model and limits are chosen in the settings screen, which also has a button to test the connection.

An API key is kept encrypted with the system keychain and is passed to the backend in memory only. It is never written to the database or to a log, and the interface can store a key but never read one back. If the system cannot encrypt, the key is not saved.

Embedding and search always run locally. With a remote model, only the retrieved excerpts (not the whole repository) leave the machine, and only when the "Send code to remote model" switch is on. A model running on the same computer needs no such permission.

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

To use the answer step, open **Answer model** in the sidebar, choose a provider and paste the key. For a model on your own computer, such as Ollama, no key is needed. As an alternative for Groq, export `GROQ_API_KEY` in the terminal before `npm run dev`.

## Testing

```bash
uv run pytest -q          # backend
npm run test:js           # Electron integration and interface logic
npx tsc -b                # type check
```

## Roadmap

- Save conversations between sessions and let follow-up questions use the earlier ones.
- Show the code behind a source in the interface.
- Bundle the Python backend into the installer.
