"""First run: why the embedder is not ready is in `/health` (`problem`); these two addresses download the missing embedding model through Ollama.

`POST /setup/pull-model` takes NO model name: it downloads the embedder's own model, so it cannot be used to make Ollama fetch anything else. It answers 202 at
once and the download goes on in the background; `GET /setup/pull-model` reads the progress. When the download is done the embedder is warmed again by itself.
"""
from fastapi import APIRouter, Depends

from model_pull import PullNotAvailable
from services import Services, get_services

router = APIRouter()


def _puller(services: Services):
    if services.puller is None:
        raise PullNotAvailable("this setup does not use Ollama")
    return services.puller


@router.post("/setup/pull-model", status_code=202)
def start_pull(services: Services = Depends(get_services)):
    return _puller(services).start()


@router.get("/setup/pull-model")
def pull_status(services: Services = Depends(get_services)):
    return _puller(services).status()
