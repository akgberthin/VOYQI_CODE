import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import inventory_engine

router = APIRouter(prefix="/api/v1/dms/vehicles", tags=["inventory"])


class VehicleCreate(BaseModel):
    rooftop_id: uuid.UUID
    vin: str
    description: str
    condition: str
    price_usd: float
    cost_usd: float
    days_in_inventory: int = 0


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    vin: str
    description: str
    condition: str
    price_usd: float
    cost_usd: float
    days_in_inventory: int
    status: str


@router.post("", response_model=VehicleOut, status_code=201)
async def create_vehicle(body: VehicleCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await inventory_engine.create_vehicle(db, tenant_id, body.rooftop_id, body.vin, body.description, body.condition, body.price_usd, body.cost_usd, body.days_in_inventory)


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await inventory_engine.list_vehicles(db, tenant_id)


@router.get("/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await inventory_engine.get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


class ApplyPriceRequest(BaseModel):
    price_usd: float
    reasoning: str = ""


@router.post("/{vehicle_id}/price", response_model=VehicleOut)
async def apply_price(vehicle_id: uuid.UUID, body: ApplyPriceRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The terminal action for every pricing path — manual re-price,
    the Pricing Intelligence Agent's retail-pricing evaluation, or the
    aging sweep. Applies immediately, no acceptance step."""
    try:
        return await inventory_engine.apply_price(db, tenant_id, vehicle_id, body.price_usd, body.reasoning)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/evaluate-aging-sweep", response_model=list[VehicleOut])
async def evaluate_aging_sweep(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: applies markdowns to every in-stock vehicle
    at or beyond the aging threshold, in this same call."""
    return await inventory_engine.evaluate_aging_sweep(db, tenant_id)


class BookValueQuoteCreate(BaseModel):
    vin: str
    source: str
    wholesale_usd: float
    retail_usd: float


@router.post("/book-value-quotes", status_code=201)
async def add_book_value_quote(body: BookValueQuoteCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    quote = await inventory_engine.add_book_value_quote(db, tenant_id, body.vin, body.source, body.wholesale_usd, body.retail_usd)
    return {"id": str(quote.id), "vin": quote.vin}


@router.get("/book-value-quotes/{vin}/average")
async def get_book_value_average(vin: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Called by the Pricing Intelligence Agent's retail-pricing
    capability — this is real market data, unlike trade appraisal's
    market comps, which don't exist yet."""
    return await inventory_engine.get_book_value_average(db, tenant_id, vin)


class VHRRequest(BaseModel):
    provider: str
    title_status: str
    accident_count: int = 0
    odometer_rollback: bool = False


@router.post("/{vehicle_id}/vehicle-history-report")
async def pull_vhr(vehicle_id: uuid.UUID, body: VHRRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: pulling the report immediately drafts a
    disclosure (if needed) and syncs the listing — no separate click.
    Title disclosure is a legal obligation, not a security matter."""
    try:
        report, vehicle = await inventory_engine.pull_vhr(db, tenant_id, vehicle_id, body.provider, body.title_status, body.accident_count, body.odometer_rollback)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": str(report.id), "title_status": report.title_status.value, "disclosure_text": report.disclosure_text,
        "listing_synced": report.listing_synced, "vehicle_price_usd": vehicle.price_usd,
    }
