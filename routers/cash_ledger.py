from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_user_context, require_role
from services.cash_ledger_service import CashLedgerService

router = APIRouter(prefix="/api/cash-ledger", tags=["Cash Ledger"])


class CashEntryCreate(BaseModel):
    store_id: str
    entry_type: str
    amount: float = Field(gt=0)
    note: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class DailyClosePayload(BaseModel):
    store_id: str
    as_of_date: date


@router.post("/entries")
async def create_cash_entry(payload: CashEntryCreate, context: dict = Depends(get_user_context)):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        row = CashLedgerService.add_entry(
            {
                "business_id": context["business_id"],
                "store_id": payload.store_id,
                "entry_type": payload.entry_type,
                "amount": payload.amount,
                "note": payload.note,
                "reference_type": payload.reference_type,
                "reference_id": payload.reference_id,
            }
        )
        return row
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/totals/{store_id}")
async def get_totals(store_id: str, context: dict = Depends(get_user_context)):
    if store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    return CashLedgerService.get_current_totals(context["business_id"], store_id)


@router.post("/daily-close")
async def daily_close(
    payload: DailyClosePayload,
    business_id: str = Depends(require_role(["owner", "admin"])),
    context: dict = Depends(get_user_context)
):
    if payload.store_id not in context.get("allowed_stores", []):
        raise HTTPException(status_code=403, detail="Access denied for this store")
    try:
        return CashLedgerService.daily_close(
            business_id=business_id,
            store_id=payload.store_id,
            as_of_date=payload.as_of_date,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
