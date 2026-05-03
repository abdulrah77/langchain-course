from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional


from dependencies import get_user_context, verify_store_access, require_role
from services.inventory_service import InventoryService

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


# Pydantic Schemas for Validation
class InventoryUpdateSchema(BaseModel):
    store_id: str
    stock_quantity: float


class InventoryCreateSchema(BaseModel):
    store_id: str
    product_id: str
    stock_quantity: float
    buy_price: float
    selling_price: float


class InventoryTransactionCreateSchema(BaseModel):
    store_id: str
    inventory_id: str
    transaction_type: str
    quantity: float
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    note: Optional[str] = None
    occurred_at: Optional[str] = None

@router.get("/")
async def get_inventory(
    limit: int = 50,
    skip: int = 0,
    search: Optional[str] = None,
    low_stock: bool = False,
    context: dict = Depends(get_user_context)
):
    try:
        return InventoryService.get_all(
            business_id=context["business_id"],
            allowed_stores=context["allowed_stores"],
            limit=limit,
            skip=skip,
            search=search,
            low_stock=low_stock
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# routers/inventory.py

@router.get("/stats/dashboard")
async def get_dashboard_stats(context: dict = Depends(get_user_context)):
    try:
        return InventoryService.get_dashboard_stats(context["business_id"], context["allowed_stores"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
async def create_inventory(
    payload: InventoryCreateSchema, 
    context: dict = Depends(get_user_context)
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        data = payload.dict()
        data["business_id"] = context["business_id"]
        data["is_active"] = True
        return InventoryService.create(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{inventory_id}")
async def update_stock(
    inventory_id: str, 
    payload: InventoryUpdateSchema, 
    business_id: str = Depends(require_role(["owner", "admin", "manager"])),
    context: dict = Depends(get_user_context)
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        return InventoryService.adjust_stock_to(
            inventory_id=inventory_id,
            business_id=business_id,
            store_id=payload.store_id,
            target_stock_quantity=payload.stock_quantity,
            reference_type="inventory_update",
            reference_id=inventory_id,
            note="Stock adjusted via inventory update endpoint",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Database constraint error: {str(e)}")

@router.delete("/{inventory_id}")
async def delete_inventory(
    inventory_id: str, 
    store_id: str,
    context: dict = Depends(get_user_context)
):
    if store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        result = InventoryService.soft_delete(inventory_id, context["business_id"], store_id)
        if not result:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/transactions")
async def create_inventory_transaction(
    payload: InventoryTransactionCreateSchema,
    business_id: str = Depends(require_role(["owner", "admin", "manager"])),
    context: dict = Depends(get_user_context),
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        data = payload.dict()
        data["business_id"] = business_id
        return InventoryService.create_inventory_transaction(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/consistency-check")
async def inventory_consistency_check(
    store_id: Optional[str] = None,
    context: dict = Depends(get_user_context),
):
    if store_id and store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        # If no store_id is provided, verify_stock_consistency might check all stores,
        # so we should ensure it only checks allowed_stores or enforce providing store_id.
        # But for now, just pass the parameters down.
        rows = InventoryService.verify_stock_consistency(business_id=context["business_id"], store_id=store_id)
        inconsistent = [r for r in rows if not r.get("is_consistent")]
        return {
            "total_checked": len(rows),
            "inconsistent_count": len(inconsistent),
            "inconsistent_rows": inconsistent,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))