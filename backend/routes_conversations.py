"""The conversation endpoints of a project. Plain `def`: FastAPI runs them on its thread pool."""
from fastapi import APIRouter, Depends, Response

import conversations
import db
from jobs import ProjectNotFound
from services import get_conn

router = APIRouter()


def _check_project(conn, project_id: int) -> None:
    if db.get_project(conn, project_id) is None:
        raise ProjectNotFound(f"no project {project_id}")


@router.post("/projects/{project_id}/conversations", status_code=201)
def create_conversation(project_id: int, conn=Depends(get_conn)):
    _check_project(conn, project_id)
    conversation_id = conversations.create_conversation(conn, project_id)
    return next(c for c in conversations.list_conversations(conn, project_id) if c["id"] == conversation_id)


@router.get("/projects/{project_id}/conversations")
def list_conversations(project_id: int, conn=Depends(get_conn)):
    _check_project(conn, project_id)
    return conversations.list_conversations(conn, project_id)


@router.get("/projects/{project_id}/conversations/{conversation_id}")
def get_conversation(project_id: int, conversation_id: int, conn=Depends(get_conn)):
    _check_project(conn, project_id)
    found = conversations.get_conversation(conn, project_id, conversation_id)
    if found is None:
        raise conversations.ConversationNotFound(f"no conversation {conversation_id}")
    return found


@router.delete("/projects/{project_id}/conversations/{conversation_id}", status_code=204)
def delete_conversation(project_id: int, conversation_id: int, conn=Depends(get_conn)):
    _check_project(conn, project_id)
    if not conversations.delete_conversation(conn, project_id, conversation_id):
        raise conversations.ConversationNotFound(f"no conversation {conversation_id}")
    return Response(status_code=204)
