import base64
from functools import lru_cache

from pydantic_settings import BaseSettings

# Fine for local dev only — validate_for_environment() refuses to run with them anywhere else.
DEV_JWT_SECRET = "dev_only_jwt_secret_change_me_0123456789"
DEV_CREDENTIAL_KEY = base64.urlsafe_b64encode(b"dev-only-fernet-key-do-not-use!!").decode()


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    environment: str = "dev"
    # Encrypts stored Databricks tokens (a Fernet key). Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Losing or changing it makes stored tokens unreadable — admins would re-enter them.
    credential_encryption_key: str = DEV_CREDENTIAL_KEY
    # Origins the browser-based frontend is served from (comma-separated). The frontend runs on
    # its own origin (a different port), so the browser enforces CORS on every request it makes here.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_environment(self) -> None:
        if self.environment != "dev" and (
            self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise RuntimeError(
                "JWT_SECRET must be a unique value of at least 32 characters "
                f"when ENVIRONMENT is '{self.environment}' (not 'dev')."
            )
        if self.environment != "dev" and self.credential_encryption_key == DEV_CREDENTIAL_KEY:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY must be set to a real Fernet key "
                f"when ENVIRONMENT is '{self.environment}' (not 'dev')."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
