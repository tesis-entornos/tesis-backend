import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from sqlmodel import col, func, select

from app.api.deps import CacheDep, CurrentUser, SessionDep
from app.core.cache import ITEMS_NAMESPACE, CacheStatus
from app.models import Item, ItemCreate, ItemPublic, ItemsPublic, ItemUpdate, Message

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=ItemsPublic)
def read_items(
    session: SessionDep,
    current_user: CurrentUser,
    cache: CacheDep,
    response: Response,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """
    Retrieve items.
    """
    scope = "all" if current_user.is_superuser else str(current_user.id)
    lookup = cache.lookup(ITEMS_NAMESPACE, "list", scope, str(skip), str(limit))
    response.headers["X-App-Cache"] = lookup.status
    if lookup.status is CacheStatus.HIT:
        return ItemsPublic.model_validate(lookup.value)

    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Item)
        count = session.exec(count_statement).one()
        statement = (
            select(Item).order_by(col(Item.created_at).desc()).offset(skip).limit(limit)
        )
        items = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(Item)
            .where(Item.owner_id == current_user.id)
        )
        count = session.exec(count_statement).one()
        statement = (
            select(Item)
            .where(Item.owner_id == current_user.id)
            .order_by(col(Item.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        items = session.exec(statement).all()

    items_public = [ItemPublic.model_validate(item) for item in items]
    result = ItemsPublic(data=items_public, count=count)
    cache.store(lookup, result.model_dump(mode="json"))
    return result


@router.get("/{id}", response_model=ItemPublic)
def read_item(
    session: SessionDep,
    current_user: CurrentUser,
    cache: CacheDep,
    response: Response,
    id: uuid.UUID,
) -> Any:
    """
    Get item by ID.
    """
    lookup = cache.lookup(ITEMS_NAMESPACE, "item", str(id))
    response.headers["X-App-Cache"] = lookup.status
    if lookup.status is CacheStatus.HIT:
        item = ItemPublic.model_validate(lookup.value)
    else:
        db_item = session.get(Item, id)
        if not db_item:
            raise HTTPException(status_code=404, detail="Item not found")
        item = ItemPublic.model_validate(db_item)
        cache.store(lookup, item.model_dump(mode="json"))
    # Permissions are always checked, also for cached items
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
def create_item(
    *, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate
) -> Any:
    """
    Create new item.
    """
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.put("/{id}", response_model=ItemPublic)
def update_item(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    item_in: ItemUpdate,
) -> Any:
    """
    Update an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = item_in.model_dump(exclude_unset=True)
    item.sqlmodel_update(update_dict)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{id}")
def delete_item(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(item)
    session.commit()
    return Message(message="Item deleted successfully")
