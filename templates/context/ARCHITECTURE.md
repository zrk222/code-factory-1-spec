# Architecture

## Tech stack (mandated versions)
- 

## System boundaries
- Vertical Slice Architecture: all code for one transaction lives in one directory under `slices/`.
- Skeleton (human-owned): `skeleton/` — base classes, auth, telemetry, security filters.
- Tissue (agent-generated): `slices/**` implementation logic only.

## Fixed execution sequence (Template Method)
authenticate -> validate -> execute -> log — agents fill ONLY execute().
