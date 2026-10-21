from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.routers import account, assets, auth, bronze, connection, discovery, members, pipelines, runs, silver, sources
from app.services.errors import ServiceError


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().validate_for_environment()
    yield


DESCRIPTION = """
**Trying it out here**

1. `POST /auth/register` creates your account and organization, and returns an `access_token`.
2. Click **Authorize** (top right) and enter your email as *username* plus your password. Leave
   *client_id* and *client_secret* empty. Running `POST /auth/login` with *Try it out* does **not**
   log this page in; only **Authorize** does. A closed padlock on an endpoint means you're logged in.
3. Tokens expire (see `JWT_EXPIRE_MINUTES`); Authorize again if you start getting 401.

`X-Org-Id` is optional: it's only needed if your account belongs to more than one organization.
"""

app = FastAPI(title="Ingestion SaaS control plane", version="0.1.0", description=DESCRIPTION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,  # auth is a bearer token (X-Org-Id/Authorization headers), never cookies
    allow_methods=["*"],
    allow_headers=["Authorization", "X-Org-Id", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(account.router)
app.include_router(members.router)
app.include_router(sources.router)
app.include_router(connection.router)
app.include_router(discovery.router)
app.include_router(pipelines.router)
app.include_router(runs.router)
app.include_router(assets.router)
app.include_router(bronze.router)
app.include_router(silver.router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status_code)


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
