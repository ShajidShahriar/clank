"""Manual script, not a test: create a project, a conversation and messages in app.db (writes to app.db in the current folder).
Run from backend/:  uv run python scripts/test_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import init_db, create_project, create_conversation, create_message, get_conversations_for_project, get_messages_for_conversation

init_db()

pid = create_project("Test Project", "/Users/you/some-repo")
print("Created project:", pid)

cid = create_conversation(pid, "First conversation")
print("Created conversation:", cid)

create_message(cid, "user", "What does this repo do?")
create_message(cid, "assistant", "It's a RAG-based coding assistant.")

print("Conversations for project:", get_conversations_for_project(pid))
print("Messages for conversation:", get_messages_for_conversation(cid))
