# Backend rules (FastAPI, SQLAlchemy async, Postgres RLS)

## Data

- **Migrations assume a populated database.** A data migration touches only the rows it must
  (e.g. 0009 adds starter categories only to households with none), records what it changed in
  `migration_backup` so `downgrade` can restore exactly, and never reverts a value changed
  after it ran. Every data migration gets a case in
  `tests/integration/test_migrations_live_data.py` (migrate a seeded scratch DB up and down).
- **Migrations are shape-detecting**: `0001` builds from live metadata, so a new table may
  already exist on a fresh DB — guard the create, then add the RLS policy **before** the grant
  (see 0003/0010). Every household table carries `household_id` and an RLS policy.
- **A migration that needs app data keeps a frozen copy** of it, and a test holds the copy
  equal to the app's at that revision (0009).
- **Provenance** (ADR-0007/0019/0049): user > rule > auto > provider. Automatic writers fill
  only what is blank or theirs.

## API

- **A new response field needs a policy** in `app/agent/policies.py`, or agents never see it
  and `tests/unit/test_agent_anonymize.py` fails. `Keep` is never allowed on a string; names
  and free text are `Pseudonym`/`Label`. A binary or raw route goes in `dispatch.EXCLUDED`
  with its reason.
- **After any API change regenerate the contract** and read the diff:

  ```bash
  METALMARK_ENV=test uv run python -c "
  import yaml, json
  from app.main import create_app
  spec = json.loads(json.dumps(create_app().openapi(), default=str))
  open('../contracts/openapi.yaml', 'w').write(
      yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100))"
  ```

- A 204 returns `Response(status_code=204)` — no JSON content type on an empty body.

## Checks

```bash
uv run ruff check . && uv run pytest -q -p no:cacheprovider
```

Integration tests start their own Postgres (testcontainers); the full suite is ~5 minutes.
