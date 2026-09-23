-- What migrations 0006 and 0007 will change — read-only, run it BEFORE upgrading.
--
-- The deployment's `migrate` service runs `alembic upgrade head` on every
-- `docker compose up`, so pulling an image that contains 0006/0007 applies them to
-- the household's data at once. This prints every row they would touch, with its
-- value now and after, so the rule can be read against real accounts first
-- (ADR-0043, ADR-0044). It writes nothing.
--
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < scripts/preview_balance_migrations.sql
--
-- The selections are copied from the two migrations; keep them in step if either
-- changes before it ships.

\echo '== 0006: synced investment accounts with no position become stated =='
SELECT a.name, a.institution, a.current_balance, a.balance_date,
       a.balance_source AS source_now, 'stated' AS source_after,
       NOT EXISTS (SELECT 1 FROM balance_snapshots s WHERE s.account_id = a.id)
           AS gets_one_snapshot_from_current_balance
FROM accounts a
WHERE a.type = 'investment'
  AND a.balance_source = 'derived'
  AND a.external_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM holdings h WHERE h.account_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM investment_transactions t WHERE t.account_id = a.id)
ORDER BY a.name;

\echo '== 0007: liability balances that will be negated (amount owed -> signed) =='
WITH targets AS (
    SELECT a.id FROM accounts a
    WHERE a.type IN ('credit', 'loan')
      AND (a.current_balance > 0
           OR EXISTS (SELECT 1 FROM balance_snapshots s
                      WHERE s.account_id = a.id AND s.balance > 0))
      AND (a.external_key IS NULL
           OR a.current_balance < 0
           OR EXISTS (SELECT 1 FROM balance_snapshots s
                      WHERE s.account_id = a.id AND s.balance < 0))
)
SELECT a.name, 'current_balance' AS what, a.balance_date AS on_date,
       a.current_balance AS now, -a.current_balance AS after
FROM accounts a JOIN targets t ON t.id = a.id
WHERE a.current_balance > 0
UNION ALL
SELECT a.name, 'snapshot', s.balance_date, s.balance, -s.balance
FROM balance_snapshots s JOIN targets t ON t.id = s.account_id
JOIN accounts a ON a.id = s.account_id
WHERE s.balance > 0
ORDER BY 1, 2, 3;

\echo '== 0007: liabilities LEFT AS THEY ARE though positive (synced, never negative) =='
\echo '   These still count in the household''s favour. Check each one; sync will warn.'
SELECT a.name, a.institution, a.current_balance, a.balance_date
FROM accounts a
WHERE a.type IN ('credit', 'loan')
  AND a.external_key IS NOT NULL
  AND a.current_balance >= 0
  AND NOT EXISTS (SELECT 1 FROM balance_snapshots s WHERE s.account_id = a.id AND s.balance < 0)
  AND (a.current_balance > 0
       OR EXISTS (SELECT 1 FROM balance_snapshots s WHERE s.account_id = a.id AND s.balance > 0))
ORDER BY a.name;

\echo '== FX pairs stored both ways round (ADR-0046: the more recent now wins) =='
\echo '   The worker recomputes cached amounts once on start; conversions of these pairs may move.'
SELECT a.base_currency || '->' || a.quote_currency AS stored, max(a.rate_date) AS latest,
       b.base_currency || '->' || b.quote_currency AS also_stored, max(b.rate_date) AS latest_other
FROM fx_rates a JOIN fx_rates b
  ON a.base_currency = b.quote_currency AND a.quote_currency = b.base_currency
WHERE a.base_currency < a.quote_currency
GROUP BY a.base_currency, a.quote_currency, b.base_currency, b.quote_currency;

\echo '== Accounts typed ''other'' with a negative balance: probably a card or loan =='
\echo '   Counted correctly either way; retype them in Accounts for the right label.'
SELECT a.name, a.institution, a.current_balance FROM accounts a
WHERE a.type = 'other' AND a.current_balance < 0 ORDER BY a.name;
