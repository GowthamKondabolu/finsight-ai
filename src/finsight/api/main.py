"""FinSight AI FastAPI application."""

from fastapi import FastAPI

from finsight import __version__
from finsight.api.schemas import HealthResponse
from finsight.config.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application using explicit or environment-based settings."""

    resolved_settings = settings or get_settings()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        description="Agentic financial risk intelligence over public SEC filings.",
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["operations"],
    )
    async def health() -> HealthResponse:
        """Return service identity and runtime environment."""

        return HealthResponse(
            service=resolved_settings.app_name,
            version=__version__,
            environment=resolved_settings.environment,
        )

    return application


app = create_app()
