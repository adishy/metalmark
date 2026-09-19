# ADR 0003: Boring stack — FastAPI + Postgres + React/TS + ECharts

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0004, ADR-0005, ARCHITECTURE.md §1

## Context

The owner asked for solid, off-the-shelf, easy-to-maintain technologies. We need a typed backend with strong
data tooling, a capable SPA/PWA frontend, and one charting library that covers pie/bar/line **and** Sankey.

## Decision

We will use **Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2** on the backend,
**PostgreSQL 16** for data, **React + TypeScript + Vite + TanStack Query + Tailwind/Radix** on the frontend,
and **Apache ECharts** as the single charting library (it covers Sankey with built-in drill-down, unlike
Recharts/Chart.js). Swipe UI uses framer-motion + @use-gesture; PWA via vite-plugin-pwa.

## Consequences

- **Positive:** mature, well-documented, large talent pool; one chart dependency for all reporting incl. Sankey.
- **Negative / costs:** ECharts is imperative (not React-native) — wrap it carefully and memoize (perf ADRs).
- **Follow-ups:** OpenAPI-generated TS client keeps FE/BE in sync; contract tests enforce it.
