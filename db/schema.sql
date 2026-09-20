-- Agent Governance Phase 2 Schema
-- PostgreSQL persistence for repos, jobs, intelligence, receipts

-- Repo Registry
CREATE TABLE IF NOT EXISTS repos (
    repo_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    canonical_path TEXT NOT NULL UNIQUE,
    remote_url TEXT,
    default_branch TEXT DEFAULT 'main',
    current_branch TEXT,
    head_hash TEXT,
    head_message TEXT,
    dirty BOOLEAN DEFAULT FALSE,
    languages JSONB DEFAULT '[]',
    frameworks JSONb DEFAULT '[]',
    package_managers JSONB DEFAULT '[]',
    build_system JSONB DEFAULT '[]',
    test_commands JSONB DEFAULT '[]',
    lint_commands JSONB DEFAULT '[]',
    typecheck_commands JSONB DEFAULT '[]',
    deployment_platform TEXT,
    database_deps JSONB DEFAULT '[]',
    containerized BOOLEAN DEFAULT FALSE,
    ci_provider TEXT,
    last_commit_at TIMESTAMPTZ,
    last_inspection_at TIMESTAMPTZ,
    last_mutation_at TIMESTAMPTz,
    health_state JSONB DEFAULT '{}',
    risk_classification TEXT DEFAULT 'unknown',
    importance_classification TEXT DEFAULT 'normal',
    available_actions JSONB DEFAULT '[]',
    known_dependencies JSONB DEFAULT '[]',
    related_repos JSONB DEFAULT '[]',
    config_overrides JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Repo Intelligence Index
CREATE TABLE IF NOT EXISTS repo_intelligence (
    repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
    index_version INTEGER DEFAULT 1,
    entry_points JSONB DEFAULT '[]',
    major_modules JSONB DEFAULT '[]',
    services JSONB DEFAULT '[]',
    routes JSONB DEFAULT '[]',
    api_endpoints JSONB DEFAULT '[]',
    db_layer JSONB DEFAULT '{}',
    auth_layer JSONB DEFAULT '{}',
    billing_layer JSONB DEFAULT '{}',
    external_integrations JSONB DEFAULT '[]',
    background_workers JSONB DEFAULT '[]',
    queues JSONB DEFAULT '[]',
    test_structure JSONB DEFAULT '{}',
    deployment_config JSONB DEFAULT '{}',
    ci_config JSONB DEFAULT '{}',
    observability JSONB DEFAULT '{}',
    security_components JSONB DEFAULT '[]',
    repo_map JSONB DEFAULT '{}',
    stale BOOLEAN DEFAULT FALSE,
    indexed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id)
);

-- Repo file index for symbol/search
CREATE TABLE IF NOT EXISTS repo_files (
    id SERIAL PRIMARY KEY,
    repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_hash TEXT,
    language TEXT,
    size_bytes BIGINT,
    symbols JSONB DEFAULT '[]',
    imports JSONB DEFAULT '[]',
    exports JSONB DEFAULT '[]',
    functions JSONB DEFAULT '[]',
    classes JSONB DEFAULT '[]',
    routes_found JSONB DEFAULT '[]',
    last_indexed TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(repo_id, file_path)
);

-- Cross-repo relationships
CREATE TABLE IF NOT EXISTS repo_relationships (
    id SERIAL PRIMARY KEY,
    source_repo TEXT REFERENCES repos(repo_id),
    target_repo TEXT REFERENCES repos(repo_id),
    relationship_type TEXT NOT NULL,  -- 'shared_pkg', 'shared_api', 'shared_schema', 'fork', 'dependency', 'copy_paste', 'shared_infra'
    evidence JSONB DEFAULT '{}',
    confidence REAL DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_repo, target_repo, relationship_type)
);

-- Durable Job Queue
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    repo_id TEXT,
    job_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'QUEUED',  -- QUEUED, POLICY_CHECK, PLANNING, RUNNING, VERIFYING, SUCCEEDED, FAILED, BLOCKED, CANCELLED, ROLLING_BACK, ROLLED_BACK
    priority INTEGER DEFAULT 5,
    autonomy_level INTEGER DEFAULT 0,
    provider TEXT,
    model TEXT,
    node TEXT,
    worker_id TEXT,
    starting_head TEXT,
    working_branch TEXT,
    worktree_path TEXT,
    files_examined JSONB DEFAULT '[]',
    files_changed JSONB DEFAULT '[]',
    commands_run JSONB DEFAULT '[]',
    verification_result JSONB DEFAULT '{}',
    policy_decisions JSONb DEFAULT '[]',
    blocked_operations JSONB DEFAULT '[]',
    resource_usage JSONB DEFAULT '{}',
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    timeout_seconds INTEGER DEFAULT 600,
    depends_on TEXT[],  -- job_ids this depends on
    idempotency_key TEXT,
    duration_ms BIGINT,
    commit_hash TEXT,
    diff_hash TEXT,
    rollback_ref TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Action Receipts V2
CREATE TABLE IF NOT EXISTS action_receipts (
    receipt_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(job_id),
    repo_id TEXT,
    user_request TEXT,
    interpreted_action TEXT,
    autonomy_level INTEGER DEFAULT 0,
    agent_laws_version TEXT,
    provider TEXT,
    model TEXT,
    node TEXT,
    starting_head TEXT,
    working_branch TEXT,
    worktree_path TEXT,
    files_examined JSONB DEFAULT '[]',
    files_changed JSONB DEFAULT '[]',
    commands JSONB DEFAULT '[]',
    verification JSONB DEFAULT '{}',
    policy_decisions JSONB DEFAULT '[]',
    blocked_operations JSONB DEFAULT '[]',
    resource_usage JSONB DEFAULT '{}',
    duration_ms BIGINT,
    ending_head TEXT,
    commit_hash TEXT,
    diff_hash TEXT,
    rollback_ref TEXT,
    final_status TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Model Capability Registry
CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    provider TEXT NOT NULL,
    context_capacity INTEGER,
    supports_tools BOOLEAN DEFAULT FALSE,
    supports_vision BOOLEAN DEFAULT FALSE,
    coding_capability TEXT DEFAULT 'unknown',
    reasoning_capability TEXT DEFAULT 'unknown',
    routing_classes JSONB DEFAULT '[]',
    avg_latency_ms REAL,
    vram_required_mb INTEGER,
    availability BOOLEAN DEFAULT TRUE,
    task_success_count INTEGER DEFAULT 0,
    task_total_count INTEGER DEFAULT 0,
    last_checked TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Routing feedback log
CREATE TABLE IF NOT EXISTS routing_log (
    id SERIAL PRIMARY KEY,
    job_id TEXT,
    model_id TEXT,
    task_class TEXT,
    provider TEXT,
    duration_ms BIGINT,
    resource_usage JSONB DEFAULT '{}',
    attempt_count INTEGER DEFAULT 1,
    verification_passed BOOLEAN,
    rollback_occurred BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Repo health findings
CREATE TABLE IF NOT EXISTS health_findings (
    id SERIAL PRIMARY KEY,
    repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,  -- 'git', 'build', 'tests', 'lint', 'typecheck', 'deps', 'security', 'ci', 'deploy', 'config', 'secrets', 'dead_code'
    severity TEXT NOT NULL,  -- 'info', 'warning', 'error', 'critical'
    confidence REAL DEFAULT 1.0,
    description TEXT NOT NULL,
    evidence JSONB DEFAULT '{}',
    file_path TEXT,
    line_number INTEGER,
    recommended_action TEXT,
    can_fix_safely BOOLEAN DEFAULT FALSE,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Priority backlog
CREATE TABLE IF NOT EXISTS backlog_items (
    id SERIAL PRIMARY KEY,
    repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
    finding_id INTEGER REFERENCES health_findings(id),
    title TEXT NOT NULL,
    description TEXT,
    severity TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    blast_radius TEXT DEFAULT 'small',
    effort_estimate TEXT DEFAULT 'unknown',
    verification_available BOOLEAN DEFAULT FALSE,
    reversible BOOLEAN DEFAULT TRUE,
    priority_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'open',  -- open, in_progress, resolved, wontfix
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Repo locks for mutation conflicts
CREATE TABLE IF NOT EXISTS repo_locks (
    repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
    lock_type TEXT NOT NULL,  -- 'read', 'write'
    job_id TEXT,
    worker_id TEXT,
    acquired_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (repo_id, lock_type)
);

-- Workers
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    node TEXT NOT NULL,
    capabilities JSONB DEFAULT '[]',
    current_job_id TEXT,
    status TEXT DEFAULT 'idle',  -- idle, busy, offline, draining
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_repo ON jobs(repo_id);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_receipts_job ON action_receipts(job_id);
CREATE INDEX IF NOT EXISTS idx_receipts_repo ON action_receipts(repo_id);
CREATE INDEX IF NOT EXISTS idx_health_repo ON health_findings(repo_id);
CREATE INDEX IF NOT EXISTS idx_backlog_priority ON backlog_items(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_routing_model ON routing_log(model_id);
CREATE INDEX IF NOT EXISTS idx_files_repo ON repo_files(repo_id);