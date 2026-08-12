CREATE TABLE IF NOT EXISTS plan_authority_requests (
    request_id text PRIMARY KEY,
    plan_commit text NOT NULL,
    solution_hash text NOT NULL,
    closure_hash text NOT NULL,
    graph_id text NOT NULL,
    object_generation text NOT NULL,
    effect_kind text NOT NULL CHECK (effect_kind IN ('coding-release','bounded-flake-push','flake-switch-record-only','dependency-resubmit')),
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
    request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
    decision_kind text CHECK (decision_kind IN ('approve','hold','reconcile')),
    decided_by text NOT NULL,
    reason text NOT NULL,
    decided_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_authority_effect_receipts (
    receipt_id uuid PRIMARY KEY,
    request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
    effect_hash text NOT NULL,
    effect_generation text NOT NULL,
    consumed_at timestamptz NOT NULL
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
