"""Invalidate cached items whenever a transaction that changed items commits.

Listening to the ORM session (instead of invalidating in each route) covers
every write path: API routes, ``crud`` helpers, ORM cascades and bulk
``UPDATE``/``DELETE`` statements such as the one used when deleting a user.
"""

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction

from app.core.cache import ITEMS_NAMESPACE, cache
from app.models import Item, User

_PENDING_KEY = "cache_namespaces_to_invalidate"


def _mark(session: Session, namespace: str) -> None:
    session.info.setdefault(_PENDING_KEY, set()).add(namespace)


@event.listens_for(Session, "after_flush")
def _collect_flushed_changes(session: Session, _flush_context: UOWTransaction) -> None:
    changed = [*session.new, *session.dirty, *session.deleted]
    # Deleting a user deletes their items (cascade)
    if any(isinstance(obj, Item) for obj in changed) or any(
        isinstance(obj, User) for obj in session.deleted
    ):
        _mark(session, ITEMS_NAMESPACE)


@event.listens_for(Session, "do_orm_execute")
def _collect_bulk_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    if any(mapper.class_ in (Item, User) for mapper in state.all_mappers):
        _mark(state.session, ITEMS_NAMESPACE)


@event.listens_for(Session, "after_commit")
def _invalidate_after_commit(session: Session) -> None:
    for namespace in session.info.pop(_PENDING_KEY, set()):
        cache.invalidate(namespace)


@event.listens_for(Session, "after_rollback")
def _discard_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
