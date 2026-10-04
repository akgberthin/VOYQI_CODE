# security-compliance-svc

Compliance & Risk Intelligence — the platform's ABAC policy-grant
gate. Port `8031`.

Lean foundation per the client's phased plan: this is the deployable
service scaffold (Phase 1); deeper modules (full audit-log surfacing,
policy authoring UI backing, cross-service policy sync) are Phase 2.

## The hard, unconditional gate

`docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4: *"actually
applying an ABAC policy grant is tier 3, hard-coded"* — no risk score,
no threshold, no AI code path reaches it. This is the same shape as
the RBAC Super Admin Panel's own gate: **it structurally requires a
named accountable role and a prior simulation, full stop.**

`src/services/grant_engine.apply_grant` is the only place a
`PolicyGrantRequest` can become `approved`, and it refuses to do so
unless both are true:

1. **`simulated` is `True`** — `POST /grants/{id}/simulate` must have
   run first, recording what applying the grant would mean (whether
   the policy is active, whether the same subject already has a
   conflicting pending/approved request).
2. **`accountable_role` is supplied** — a named human role, not just
   "someone clicked approve."

Both checks raise before anything else executes — see
`NotSimulatedError` / `AccountableRoleRequiredError` in
`grant_engine.py`.

## Data model

- **CompliancePolicy** — a registered ABAC-style policy.
- **PolicyGrantRequest** — one proposed grant of a policy to a
  user/role, carrying its own simulation result and decision audit
  trail.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/security-compliance/policies` |
| `POST` / `GET` | `/api/v1/security-compliance/grants` |
| `POST` | `/api/v1/security-compliance/grants/{id}/simulate` |
| `POST` | `/api/v1/security-compliance/grants/{id}/apply` |
| `POST` | `/api/v1/security-compliance/grants/{id}/reject` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8031
pytest
```
