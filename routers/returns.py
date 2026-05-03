from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_user_context, get_current_user
from services.return_service import ReturnService

router = APIRouter(prefix="/api/returns", tags=["Returns"])


class ReturnItemCreate(BaseModel):
    product_id: str
    qty: float = Field(gt=0)
    refund_price: Optional[float] = Field(default=0, ge=0)


class ReturnCreate(BaseModel):
    store_id: str
    invoice_id: str
    reason: Optional[str] = None
    refund_amount: float = Field(default=0, ge=0)
    items: List[ReturnItemCreate]


@router.post("/")
async def create_return(
    payload: ReturnCreate,
    context: dict = Depends(get_user_context),
    current_user=Depends(get_current_user),
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        data = payload.model_dump()
        data["business_id"] = context["business_id"]
        data["created_by"] = getattr(current_user, "id", None)
        result = ReturnService.post_return(data)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/")
async def list_returns(
    context: dict = Depends(get_user_context),
):
    try:
        return ReturnService.list_returns(context["business_id"], context["allowed_stores"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
