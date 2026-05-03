from fastapi import APIRouter, HTTPException

from services.public_link_service import PublicLinkService

router = APIRouter(prefix="/public", tags=["Public Links"])


@router.get("/inventory/{token}")
async def get_public_inventory(token: str):
    try:
        return PublicLinkService.get_inventory_snapshot(token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
