"""Track the records created during the test run and delete only those.

The tests call the API in-process (TestClient), so every insert, done by
the application or by test helpers, goes through a SQLAlchemy session in
this process. A listener records the ids of the new users, items and files,
and ``delete_created_records`` removes exactly those at the end of the run.
Data that existed before the run (or is created by others meanwhile) is
never deleted.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import UOWTransaction
from sqlmodel import Session, col, delete, select

from app.core.storage import FileStorage
from app.models import File, Item, User


@dataclass
class CreatedRecords:
    users: set[uuid.UUID] = field(default_factory=set)
    items: set[uuid.UUID] = field(default_factory=set)
    files: set[uuid.UUID] = field(default_factory=set)

    def record(self, session: OrmSession, _flush_context: UOWTransaction) -> None:
        for obj in session.new:
            if isinstance(obj, User):
                self.users.add(obj.id)
            elif isinstance(obj, Item):
                self.items.add(obj.id)
            elif isinstance(obj, File):
                self.files.add(obj.id)


@contextmanager
def track_created_records() -> Iterator[CreatedRecords]:
    records = CreatedRecords()
    event.listen(OrmSession, "after_flush", records.record)
    try:
        yield records
    finally:
        event.remove(OrmSession, "after_flush", records.record)


def delete_created_records(
    session: Session, records: CreatedRecords, storage: FileStorage | None
) -> None:
    session.rollback()
    # Objects first, then rows: metadata never points to a deleted object
    if records.files:
        keys = session.exec(
            select(File.s3_key).where(col(File.id).in_(records.files))
        ).all()
        if storage is not None:
            storage.delete_many(keys)
        session.exec(delete(File).where(col(File.id).in_(records.files)))
    if records.items:
        session.exec(delete(Item).where(col(Item.id).in_(records.items)))
    if records.users:
        # Remaining items/files of these users are removed by ON DELETE CASCADE
        session.exec(delete(User).where(col(User.id).in_(records.users)))
    session.commit()
