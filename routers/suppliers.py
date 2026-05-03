from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from dependencies import get_current_business_id
from services.supplier_service import SupplierService

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])

class SupplierCreate(BaseModel):
    name: str
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    gst_number: Optional[str] = None
    address: Optional[str] = None

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

@router.get("/")
async def get_suppliers(business_id: str = Depends(get_current_business_id)):
    return SupplierService.get_all(business_id)

@router.post("/")
async def create_supplier(payload: SupplierCreate, business_id: str = Depends(get_current_business_id)):
    data = payload.dict()
    data["business_id"] = business_id
    try:
        return SupplierService.create(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{supplier_id}")
async def update_supplier(supplier_id: str, payload: SupplierUpdate, business_id: str = Depends(get_current_business_id)):
    result = SupplierService.update(supplier_id, business_id, payload.dict(exclude_unset=True))
    if not result:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return result

@router.delete("/{supplier_id}")
async def delete_supplier(supplier_id: str, business_id: str = Depends(get_current_business_id)):
    result = SupplierService.soft_delete(supplier_id, business_id)
    if not result:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return result