# VillageInsight Development Guide

## Architectural boundaries

- PostgreSQL is the only source of truth.
- Raw physical evidence is immutable.
- Hermes plans structure and semantics; it does not create source evidence or write facts.
- Published fields, templates, layout plans, identity policies, and metrics are versioned and immutable.
- Numeric answers come from deterministic metric queries, never from free-form model arithmetic.
- Open-source parsers are integrated through adapters; do not import their RAG, vector, graph, or task stacks.

## Python

- Python 3.13+.
- Keep domain code under `src/village_insight`.
- Use Pydantic at API and agent boundaries.
- Use SQLAlchemy 2 style and Alembic migrations.
- Keep blocking document and Hermes work out of API event loops.
- Create one Hermes `AIAgent` per task.

## Frontend

- React + TypeScript + Vite.
- Preserve the ledger-grid visual language in `frontend/src/styles.css`.
- Use plain user-facing terms: batches, templates, fields, reviews, questions.
- Keep independent API data loading parallel.

## Validation

Run the narrowest relevant command first:

```bash
uv run pytest
uv run ruff check .
uv run mypy src
cd frontend && npm run type-check
cd frontend && npm run test
cd frontend && npm run build
```

## File safety

- Server-side directory imports must remain under configured import roots.
- Never execute macros, formulas, embedded objects, or external links.
- Never log raw identity-card, bank-card, phone, or person-name values.
- Do not silently discard non-empty cells.
