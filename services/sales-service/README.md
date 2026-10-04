# sales-service

Sales transaction execution — the order lifecycle (draft → confirmed →
fulfilled, or cancelled at any point before fulfillment). Layer 1 core
execution, port `8009`.

This is deliberately a lean foundation, not the full "Sales" module
suite — Phase 1 of the client's own plan is a deployable SaaS-manner
scaffold per service; specific business modules (quoting, invoicing,
commission calc, etc.) are Phase 2, built on top of this once the
platform's service layer is complete.

Distinct from `crm-svc` (relationship/pipeline tracking — leads,
deals) and from `dms-svc`'s dealership-specific deal desk: a sales
order can originate from a crm-svc deal (`source_deal_id`) but is its
own record once created, and represents actual transaction execution,
not relationship management.

## Data model

- **SalesOrder** — the order record and its current status.
- **OrderStatusEvent** — append-only history of every status
  transition, so "what happened and when" survives even though
  `SalesOrder.status` itself is a mutable current-state field.

Legal transitions are enforced in `sales_order_engine.py`: draft →
confirmed → fulfilled, or → cancelled from draft/confirmed. A
fulfilled or cancelled order never reopens.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/sales/orders` |
| `GET` | `/api/v1/sales/orders/{id}` |
| `POST` | `/api/v1/sales/orders/{id}/transition` |
| `GET` | `/api/v1/sales/orders/{id}/history` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8009
pytest
```
