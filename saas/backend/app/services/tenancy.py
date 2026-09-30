import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Org
from app.services.errors import NotFound


def lock_org(db: Session, org_id: uuid.UUID) -> None:
    """Row-locks the org until the transaction ends. Anything that checks a per-org limit and then
    acts on it (member seats, pipeline slots) does so under this lock — otherwise two simultaneous
    requests both read "under the limit" and both proceed. Lock order everywhere: org first."""
    if db.scalar(select(Org.id).where(Org.id == org_id).with_for_update()) is None:
        raise NotFound("Organization not found")
