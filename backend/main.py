from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_errors import register_error_handlers
from jobs import IndexJobs
from routes_context import router as context_router
from routes_index import router as index_router
from startup_sync import StartupSync
from services import Services, default_services, get_services


def create_app(services: Services | None = None, jobs: IndexJobs | None = None, auto_sync: bool = True) -> FastAPI:
    """Build the app. Nothing touches the disk or Ollama until it starts: the real services are made in the lifespan, a test passes its own."""

    @asynccontextmanager
    async def lifespan(app):
        app.state.services = services or default_services()
        app.state.jobs = jobs or IndexJobs(app.state.services)
        app.state.services.start()
        app.state.startup_sync = StartupSync(app.state.services, app.state.jobs)
        if auto_sync:
            app.state.startup_sync.start()                         # only projects that already have an index; one at a time
        yield
        app.state.startup_sync.request_stop()                      # no new project is started ...
        app.state.jobs.shutdown()                                  # ... and a running job is asked to stop between files
        app.state.startup_sync.wait()

    app = FastAPI(lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(index_router)
    app.include_router(context_router)
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
