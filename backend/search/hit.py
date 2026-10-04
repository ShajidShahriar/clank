"""The one result of a vector search. Its own module so the ranking policy and `search` can both use it without importing each other."""
from dataclasses import dataclass


@dataclass
class Hit:
    chunk: dict        # the stored row (see chunk_store.get_chunks)
    score: float       # cosine similarity, higher is better; always the RAW score, whatever order the hit comes in
