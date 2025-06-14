CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    workflow_id TEXT, -- Optional: for linking to CPOA workflows if the key is generated there
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    locked_at TIMESTAMP WITH TIME ZONE, -- Timestamp when processing started
    status TEXT NOT NULL, -- e.g., 'processing', 'completed', 'failed'
    result_payload JSONB,
    error_payload JSONB -- Store error details if the task failed
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_created_at ON idempotency_keys(created_at);
CREATE INDEX IF NOT EXISTS idx_idempotency_keys_task_name ON idempotency_keys(task_name);
