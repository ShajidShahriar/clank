# Clank

Local code search and Q&A for software repositories.

Clank indexes a codebase and answers natural-language questions about it by retrieving the most relevant code. Embedding, storage and search all run on your machine through [Ollama](https://ollama.com). An answer model of your choice (hosted or local) then writes the answer from the retrieved code and shows the sources it used.

**Status:** in development. The retrieval engine, local API, streaming answers, saved conversations, usage tracking, desktop integration and packaging are complete and tested. The desktop interface is a working first version.

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Security and privacy](#security-and-privacy)
- [Answer models](#answer-models)
- [Evaluation](#evaluation)
- [Getting started](#getting-started)
- [Building an installer](#building-an-installer)
- [Local API](#local-api)
- [Project layout](#project-layout)
- [Data locations](#data-locations)
- [Testing](#testing)
- [Roadmap](#roadmap)

## Overview

Clank splits Python, JavaScript and TypeScript files along their syntax, so each stored piece is a whole function, method or class. Each piece is embedded with the `qwen3-embedding:0.6b` model and stored in SQLite and Chroma. A question is embedded the same way, and the closest pieces are returned, rebuilt into complete passages and trimmed to a token budget. That context goes to the answer model together with the question.

## Features

**Retrieval**
- Syntax-aware chunking with tree-sitter (Python, JS/JSX, TS/TSX). Large units are split and small neighbours grouped.
- Incremental indexing. Only changed files are processed again.
- Crash-safe indexing. A run interrupted at any point resumes where it stopped. This is covered by crash-matrix and real-kill tests.
- Staleness detection. Passages from files edited after indexing are marked as stale.
- Ranking that demotes test files and changelogs unless the question asks for them.
- Safe file discovery. Only regular files, and links that stay inside the repository, are read.

**Answers**
- Works with any OpenAI-compatible chat service. Presets for Groq, Ollama, LM Studio and a custom address.
- Streaming answers, written live as the model produces them.
- Sources listed with each answer, and a viewer for the code behind a source.
- Conversations saved per project, so you can return to earlier questions.
- Usage tracking with optional limits.

**Desktop app**
- Add a project folder, index it with live progress, cancel a run, and remove a project.
- A first-run card that helps pull the embedding model.
- A settings screen to pick the provider, address, model and limits, with a connection test.
- A switch that controls whether code may be sent to a remote model.
- An Electron shell that starts, monitors and stops the backend.

## How it works

1. **Discover.** Walk the project folder and keep supported source files.
2. **Chunk.** Parse each file and split it into whole functions, methods and classes.
3. **Embed.** Send each chunk to Ollama (`qwen3-embedding:0.6b`).
4. **Store.** Write chunk metadata to SQLite and vectors to Chroma, tracked per file so a re-run only handles what changed.
5. **Search.** Embed the question, fetch the nearest chunks, apply ranking rules and a relevance cutoff.
6. **Stitch.** Rebuild neighbouring chunks into complete passages and trim to a token budget.
7. **Answer.** Send the passages and the question to the answer model and return the answer with its sources.

## Architecture

```
Electron window  ->  Electron main process  ->  FastAPI backend (127.0.0.1)
(React, TypeScript)  starts and monitors it     chunk, embed, store, search
                     holds the token            SQLite, Chroma, Ollama
                     keeps API keys encrypted   answer model (optional, remote or local)
```

| Part | Technology |
|---|---|
| Window | React 19, TypeScript, Vite, Tailwind CSS |
| Shell | Electron, electron-builder |
| Backend | Python 3.14, FastAPI |
| Storage | SQLite (metadata, conversations, settings), Chroma (vectors) |
| Embeddings | Ollama, `qwen3-embedding:0.6b` |
| Parsing | tree-sitter |

## Security and privacy

- The backend listens on `127.0.0.1` only.
- Every request must carry a token generated for each launch, and requests with an unexpected Host or Origin header are rejected.
- The desktop window never sees the token. It reaches the backend through the Electron main process.
- Errors use a single JSON format.

Embedding and search always run locally. With a remote answer model, only the retrieved excerpts (not the whole repository) leave the machine, and only when the "Send code to remote model" switch is on. A model running on the same computer needs no such permission.

## Answer models

The answer endpoint sends the retrieved code excerpts and the question to a chat model and returns the answer with its sources. Any service that speaks the OpenAI chat protocol works. Presets are included for Groq (`openai/gpt-oss-120b` is the first supported profile), Ollama, LM Studio and a custom address (OpenRouter, OpenAI, vLLM or your own server). The provider, address, model and limits are chosen in the settings screen, which also has a button to test the connection.

An API key is kept encrypted with the system keychain and is passed to the backend in memory only. It is never written to the database or to a log, and the interface can store a key but never read one back. If the system cannot encrypt, the key is not saved.

## Evaluation

Retrieval was evaluated on three open-source repositories (this one, Flask and Express) using 20 hand-written questions. Six of the questions were held out and examined once.

| Metric | Result |
|---|---|
| Answer in the top 3 results | 16 of 20 (80%) |
| Answer in the top 10 results | 18 of 20 |
| Answer present in the text sent to the LLM (4000-token budget) | 16 of 20 |
| Median search time | about 35 ms |

The sample is small, so these figures indicate that the approach works and are not a general benchmark. Full tables, design decisions and known failures are in `backend/eval/final/RESULTS.txt`.

## Getting started

**Requirements:** Node.js, Python 3.14 or newer, [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com).

```bash
ollama pull qwen3-embedding:0.6b
npm install
uv sync
npm run dev
```

`npm run dev` opens the window. Electron starts the Python backend itself and stops it on quit.

**Using it**
1. Click to add a project folder. Indexing starts and shows progress.
2. Open **Answer model** in the sidebar, choose a provider and paste the key. For a model on your own computer, such as Ollama, no key is needed. As an alternative for Groq, export `GROQ_API_KEY` in the terminal before `npm run dev`.
3. If the model is remote, turn on **Send code to remote model**.
4. Ask a question. The answer streams in with its sources.

**Running the backend by hand**

```bash
cd backend
CLANK_API_TOKEN=<32+ characters> CLANK_DATA_DIR=<absolute folder> uv run python run.py --port 8770
```

Every request then needs the header `x-clank-token`.

## Building an installer

```bash
npm run build
```

This freezes the Python backend, type-checks, builds the window and packages the app with electron-builder (macOS, Windows and Linux targets are configured in `electron-builder.json5`).

## Local API

All routes require the `x-clank-token` header.

| Area | Routes |
|---|---|
| Health | `GET /health` |
| Projects | `POST /projects`, `GET /projects`, `GET /projects/{id}`, `DELETE /projects/{id}` |
| Indexing | `POST /projects/{id}/index`, `GET /projects/{id}/index`, `POST /projects/{id}/index/cancel`, `GET /startup-sync` |
| Search context | `POST /projects/{id}/context` |
| Answers | `POST /projects/{id}/answer`, `POST /projects/{id}/answer/stream` |
| Sources | `POST /projects/{id}/source` |
| Conversations | `POST` and `GET /projects/{id}/conversations`, `GET` and `DELETE /projects/{id}/conversations/{conversation_id}` |
| Answer model settings | `GET` and `PUT /settings/llm`, `PUT /settings/llm/key`, `POST /settings/llm/test` |
| Usage | `GET /usage`, `POST /usage/limits`, `DELETE /usage` |
| Setup | `POST` and `GET /setup/pull-model` |

## Project layout

```
backend/        Python backend (run tests from the repo root with uv)
  chunker/        syntax-aware chunking
  embedding/      embedding client (Ollama)
  vectorstore/    Chroma and in-memory stores
  indexing/       incremental, crash-safe indexing
  search/         retrieval, ranking, stitching, token budget
  llm/            answer model clients and provider profiles
  routes_*.py     FastAPI routes
  eval/           retrieval evaluation harness and results
  tests/          backend tests
electron/       Electron main process and backend supervisor
src/            React window (components, hooks, API client)
scripts/        development helpers
docs/           design notes and decisions (git-ignored except this README)
```

## Data locations

The desktop app keeps its data in the Electron user data folder (in development on macOS, `~/Library/Application Support/clank/`):

- `backend/app.db` – projects, conversations, messages, settings and the chunk index (SQLite)
- `backend/chroma/` – vectors
- `secrets/llm-keys.json` – API keys, encrypted with the system keychain

When the backend is run by hand without `CLANK_DATA_DIR`, it uses `~/.clank`.

## Testing

```bash
uv run pytest -q          # backend
npm run test:js           # Electron integration and interface logic
npx tsc -b                # type check
```

A few `live` tests need a running Ollama or a `GROQ_API_KEY` and are skipped by default. Before changing indexing, the chunk store or the vector stores, run the crash tests (`test_crash_matrix.py`, `test_real_kill.py`).

## Roadmap

- Bundle the Python backend into the installer for all platforms.
- Let follow-up questions use earlier ones in the same conversation.
- Support more languages in the chunker.
