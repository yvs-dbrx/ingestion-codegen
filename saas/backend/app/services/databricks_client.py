"""Building an authenticated Databricks client for ONE organization.

Two rules this module exists to enforce:

1. The client is always built from that org's own stored host + token, passed explicitly —
   never from environment variables or ~/.databrickscfg, and never by changing process-wide
   state (concurrent requests from different orgs would leak into each other).
2. The host is checked before the server ever connects to it. Without that, an admin could
   set the "workspace" to an internal address (cloud metadata service, the database, another
   container) and use this server to probe the network behind it (SSRF).
"""
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OrgConnection
from app.services import secrets
from app.services.errors import Conflict, Unprocessable

# Databricks-owned domains only: AWS/GCP (*.cloud.databricks.com, *.gcp.databricks.com,
# both under databricks.com), Azure (*.azuredatabricks.net), Azure China.
ALLOWED_HOST_SUFFIXES = (".databricks.com", ".azuredatabricks.net", ".databricks.azure.cn")


class InvalidHost(Unprocessable):
    pass


class NoConnection(Conflict):
    """This org hasn't connected a Databricks workspace yet."""


def normalize_host(raw: str) -> str:
    """Returns 'https://<host>' or raises InvalidHost. Accepts a bare hostname or an https URL."""
    raw = raw.strip()
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parts.scheme != "https":
        raise InvalidHost("The workspace URL must use https")
    if parts.username or parts.password:
        raise InvalidHost("The workspace URL can't contain credentials")
    if parts.port not in (None, 443):
        raise InvalidHost("The workspace URL can't specify a port")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise InvalidHost("Use just the workspace address, like https://dbc-1234.cloud.databricks.com")
    hostname = (parts.hostname or "").lower()
    # Exactly a label(s) + an allowed suffix: rules out bare IPs, 'localhost', and lookalikes
    # such as evil.com or databricks.com.evil.com (the suffix must be the END of the name).
    if not any(hostname.endswith(s) and len(hostname) > len(s) for s in ALLOWED_HOST_SUFFIXES):
        raise InvalidHost("That doesn't look like a Databricks workspace address")
    return f"https://{hostname}"


# (host, token) -> an authenticated WorkspaceClient. A dependency so tests can supply a fake;
# in production it's the real SDK, pinned to token auth so it can't fall back to server env creds.
ClientBuilder = Callable[[str, str], object]


def default_client_builder(host: str, token: str):
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=host, token=token, auth_type="pat")


def get_client_builder() -> ClientBuilder:
    return default_client_builder


def client_for_org(db: Session, org_id: uuid.UUID, builder: ClientBuilder):
    connection = db.scalar(select(OrgConnection).where(OrgConnection.org_id == org_id))
    if connection is None:
        raise NoConnection("Connect your Databricks workspace first (Connection page)")
    try:
        token = secrets.decrypt(connection.token_ciphertext)
    except secrets.CannotDecrypt:
        raise NoConnection("The stored Databricks credentials can't be read any more — reconnect the workspace (Connection page)")
    return builder(connection.host, token)
