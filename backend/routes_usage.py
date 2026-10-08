"""`GET /usage`, `POST /usage/limits`, `DELETE /usage`: what the answer model has been used for, and the person's own limits. Plain `def`.

The report is for the provider the person has CHOSEN (the saved choice), so it needs no key and no working model. Limits only warn: nothing here stops a question.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

import usage
import usage_limits
import usage_report
from llm.settings import selection_to_profile
from services import Services, get_conn, get_services

router = APIRouter()


def _now() -> datetime:
    return datetime.now().astimezone()            # the person's own time zone: their days, weeks and months


class LimitsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: dict | None                           # required: {} clears the limits, null goes back to the suggestion


def _report(conn, services: Services) -> dict:
    selection = services.llm_selection()
    return usage_report.build_report(conn, provider=selection_to_profile(selection).name, model=selection.model, now=_now())


@router.get("/usage")
def get_usage(conn=Depends(get_conn), services: Services = Depends(get_services)):
    return _report(conn, services)


@router.post("/usage/limits")
def post_limits(body: LimitsBody, conn=Depends(get_conn), services: Services = Depends(get_services)):
    usage_limits.save_limits(conn, selection_to_profile(services.llm_selection()).name, body.limits)       # raises InvalidLimits: nothing is saved
    return _report(conn, services)


@router.delete("/usage")
def delete_usage(conn=Depends(get_conn), services: Services = Depends(get_services)):
    usage.clear_calls(conn)
    return _report(conn, services)
