from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from dependencies import get_current_business_id, require_role
from services.product_service import ProductService

router = APIRouter(prefix="/api/products", tags=["Products"])


class ProductCreate(BaseModel):
    product_name: str
    category: Optional[str] = None
    brand: Optional[str] = None
    sku_code: str
    hsn_code: Optional[str] = None
    is_perishable: bool = False
    

class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    sku_code: Optional[str] = None
    is_perishable: Optional[bool] = None

@router.get("/")
async def get_products(business_id: str = Depends(get_current_business_id)):
    return ProductService.get_all(business_id)

@router.post("/")
async def create_product(payload: ProductCreate, business_id: str = Depends(get_current_business_id)):
    data = payload.dict()
    data["business_id"] = business_id
    try:
        return ProductService.create(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{product_id}")
async def update_product(product_id: str, payload: ProductUpdate, business_id: str = Depends(get_current_business_id)):
    result = ProductService.update(product_id, business_id, payload.dict(exclude_unset=True))
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return result

# routers/products.py


@router.delete("/{product_id}")
async def delete_product(
    product_id: str, 
    # Notice we swapped the dependency here!
    business_id: str = Depends(require_role(["admin", "owner"])) 
):
    result = ProductService.soft_delete(product_id, business_id)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return result