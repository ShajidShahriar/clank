from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services import Services, default_services, get_services


def create_app(services: Services | None = None) -> FastAPI:
    """Build the app. Nothing touches the disk or Ollama until it starts: the real services are made in the lifespan, a test passes its own."""

    @asynccontextmanager
    async def lifespan(app):
        app.state.services = services or default_services()
        app.state.services.start()
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health_check(services: Services = Depends(get_services)):
        return {"status": "ok", **services.status()}

    return app


app = create_app()
