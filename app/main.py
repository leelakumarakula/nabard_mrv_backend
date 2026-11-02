import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.middleware import RequestContextMiddleware

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Central MRV platform for the Coffee Climate Resilience Programme (adaptation MRV, not a carbon registry). "
        "Integration endpoints are the platform's proposed contracts; no external source API is assumed to exist."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Content-Disposition"],
)
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)

app.include_router(api_router, prefix=settings.api_v1_prefix)
