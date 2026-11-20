"""One fail-closed mutex for all operations on the fixed AWS development state."""

from sqlmodel import Session
from app.models import TerraformStateOperation

STATE_KEY = "aws-development"


def reserve_state(session: Session, owner_id: str, operation: str) -> None:
    session.add(TerraformStateOperation(key=STATE_KEY, owner_id=owner_id, operation=operation))
    session.flush()  # PK collision prevents two plan/apply queues, atomically with job creation.


def release_state(session: Session, owner_id: str) -> None:
    row = session.get(TerraformStateOperation, STATE_KEY)
    if row is not None and row.owner_id == owner_id:
        session.delete(row)


def owns_state(session: Session, owner_id: str) -> bool:
    row = session.get(TerraformStateOperation, STATE_KEY)
    return row is not None and row.owner_id == owner_id
