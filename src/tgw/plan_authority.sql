CREATE TABLE IF NOT EXISTS plan_authority_requests (
    request_id text PRIMARY KEY,
    plan_commit text NOT NULL,
    solution_hash text NOT NULL,
    closure_hash text NOT NULL,
    graph_id text NOT NULL,
    object_generation text NOT NULL,
    effect_kind text NOT NULL,
    effect_generation text NOT NULL,
    effect_hash text NOT NULL,
    effect_parameters jsonb NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL,
    requested_by text NOT NULL,
    expires_at timestamptz NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_authority_decisions (
    decision_id text PRIMARY KEY,
    request_id text NOT NULL REFERENCES plan_authority_requests(request_id),
    decision_kind text NOT NULL CHECK (decision_kind IN ('approve','hold','reconcile')),
    decided_by text NOT NULL,
    reason text NOT NULL,
    reconciliation_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    decided_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_authority_effect_receipts (
    receipt_id uuid PRIMARY KEY,
    request_id text NOT NULL REFERENCES plan_authority_requests(request_id),
    effect_hash text NOT NULL,
    effect_generation text NOT NULL,
    handler_id text,
    -- Retained solely for safe migration/read compatibility with v1 receipts.
    consumed_at timestamptz,
    started_at timestamptz NOT NULL DEFAULT NOW(),
    completed_at timestamptz,
    outcome text CHECK (outcome IN ('succeeded','retry','ambiguous','rolled_back','failed','legacy-consumed')),
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    rollback_receipt text,
    detail text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plan_authority_events (
    sequence bigserial PRIMARY KEY,
    request_id text NOT NULL REFERENCES plan_authority_requests(request_id),
    event_type text NOT NULL,
    details jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS plan_authority_events_request_idx
    ON plan_authority_events(request_id, sequence);

-- The first version represented one decision and an eager pre-handler
-- "consumption" per request.  Preserve its history while upgrading it to a
-- replay-safe lifecycle.  Constraint names are the PostgreSQL defaults from
-- the original schema; IF EXISTS keeps fresh and upgraded databases aligned.
ALTER TABLE plan_authority_decisions
    DROP CONSTRAINT IF EXISTS plan_authority_decisions_request_id_key;
ALTER TABLE plan_authority_decisions
    ADD COLUMN IF NOT EXISTS reconciliation_evidence jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE plan_authority_effect_receipts
    DROP CONSTRAINT IF EXISTS plan_authority_effect_receipts_request_id_key;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS handler_id text;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS outcome text;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS rollback_receipt text;
ALTER TABLE plan_authority_effect_receipts
    ADD COLUMN IF NOT EXISTS detail text NOT NULL DEFAULT '';

-- A legacy pre-handler receipt cannot be safely replayed.  Preserve that fact
-- as an explicit terminal state rather than pretending it completed normally.
UPDATE plan_authority_effect_receipts
   SET started_at=COALESCE(started_at, consumed_at),
       completed_at=COALESCE(completed_at, consumed_at),
       outcome=COALESCE(outcome, 'legacy-consumed')
 WHERE outcome IS NULL;
ALTER TABLE plan_authority_effect_receipts
    ALTER COLUMN started_at SET NOT NULL;
ALTER TABLE plan_authority_effect_receipts
    ALTER COLUMN started_at SET DEFAULT NOW();
ALTER TABLE plan_authority_effect_receipts
    DROP CONSTRAINT IF EXISTS plan_authority_effect_receipts_outcome_check;
ALTER TABLE plan_authority_effect_receipts
    ADD CONSTRAINT plan_authority_effect_receipts_outcome_check CHECK (
        outcome IN ('succeeded','retry','ambiguous','rolled_back','failed','legacy-consumed') OR outcome IS NULL
    );

-- This is the concurrency fence.  A completed retry releases the exact
-- approval for another attempt, while any uncompleted call stays visible and
-- blocks an unsafe automatic replay.
CREATE UNIQUE INDEX IF NOT EXISTS plan_authority_one_active_execution
    ON plan_authority_effect_receipts(request_id)
    WHERE completed_at IS NULL;
CREATE INDEX IF NOT EXISTS plan_authority_decisions_latest_idx
    ON plan_authority_decisions(request_id, decided_at DESC, decision_id DESC);
CREATE INDEX IF NOT EXISTS plan_authority_effect_receipts_latest_idx
    ON plan_authority_effect_receipts(request_id, started_at DESC, receipt_id DESC);

-- CREATE TABLE IF NOT EXISTS does not update a CHECK constraint created by an
-- older release. Replace the canonical named constraint on every schema run so
-- a live upgrade admits exactly the same closed effect registry as Python.
ALTER TABLE plan_authority_requests
    DROP CONSTRAINT IF EXISTS plan_authority_requests_effect_kind_check;
ALTER TABLE plan_authority_requests
    ADD CONSTRAINT plan_authority_requests_effect_kind_check CHECK (effect_kind IN (
        'coding-release',
        'bounded-flake-push',
        'flake-switch-record-only',
        'dependency-resubmit',
        'authority-canary',
        'approval-platform-bootstrap-deployment'
    ));
