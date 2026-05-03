from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_user_context, get_current_user
from services.purchase_service import PurchaseService

router = APIRouter(prefix="/api/purchases", tags=["Purchases"])


class PurchaseItemCreate(BaseModel):
    product_id: str
    qty: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)


class PurchaseCreate(BaseModel):
    store_id: str
    supplier_id: Optional[str] = None
    items: List[PurchaseItemCreate]
    tax: float = Field(default=0, ge=0)
    paid_amount: float = Field(default=0, ge=0)
    payment_method: str = "cash"
    source_channel: str = "api"


class PurchasePaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_mode: str
    payment_date: Optional[str] = None


@router.post("/")
async def create_purchase(
    payload: PurchaseCreate,
    context: dict = Depends(get_user_context),
    current_user=Depends(get_current_user),
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        data = payload.model_dump()
        data["business_id"] = context["business_id"]
        data["created_by"] = getattr(current_user, "id", None)
        result = PurchaseService.post_purchase(data)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{purchase_id}/payments")
async def create_purchase_payment(
    purchase_id: str,
    payload: PurchasePaymentCreate,
    context: dict = Depends(get_user_context),
    current_user=Depends(get_current_user),
):
    try:
        return PurchaseService.add_payment(
            purchase_id=purchase_id,
            business_id=context["business_id"],
            allowed_stores=context["allowed_stores"],
            amount=payload.amount,
            payment_mode=payload.payment_mode,
            payment_date=payload.payment_date,
            created_by=getattr(current_user, "id", None),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
