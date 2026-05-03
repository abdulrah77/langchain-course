from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_user_context, get_current_user
from services.invoice_service import InvoiceService

router = APIRouter(prefix="/api/invoices", tags=["Invoices"])


class InvoiceItemCreate(BaseModel):
    product_id: str
    qty: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)


class InvoiceCreate(BaseModel):
    store_id: str
    customer_name: Optional[str] = None
    items: List[InvoiceItemCreate]
    discount: float = Field(default=0, ge=0)
    tax: float = Field(default=0, ge=0)
    paid_amount: float = Field(default=0, ge=0)
    payment_method: str = "cash"
    source_channel: str = "api"


class InvoicePaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    payment_mode: str
    payment_date: Optional[str] = None


@router.post("/")
async def create_invoice(
    payload: InvoiceCreate,
    context: dict = Depends(get_user_context),
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        data = payload.model_dump()
        data["business_id"] = context["business_id"]
        result = InvoiceService.post_invoice(data)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{invoice_id}/payments")
async def create_invoice_payment(
    invoice_id: str,
    payload: InvoicePaymentCreate,
    context: dict = Depends(get_user_context),
    current_user=Depends(get_current_user),
):
    try:
        return InvoiceService.add_payment(
            invoice_id=invoice_id,
            business_id=context["business_id"],
            allowed_stores=context["allowed_stores"],
            amount=payload.amount,
            payment_mode=payload.payment_mode,
            payment_date=payload.payment_date,
            created_by=getattr(current_user, "id", None),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{invoice_id}/payments")
async def get_invoice_payments(
    invoice_id: str,
    context: dict = Depends(get_user_context),
):
    try:
        return InvoiceService.list_payments(
            invoice_id=invoice_id, 
            business_id=context["business_id"], 
            allowed_stores=context["allowed_stores"]
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
