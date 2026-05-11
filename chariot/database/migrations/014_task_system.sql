-- Chariot schema v14: task / delegation foundations

CREATE TABLE IF NOT EXISTS agent_profiles (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    prompt_bundle TEXT,
    tool_profile TEXT,
    provider_profile TEXT,
    budget TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_profiles_role ON agent_profiles(role);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_profile TEXT,
    parent_task_id TEXT,
    owner TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    artifacts TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_kind ON tasks(kind);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_profile ON tasks(agent_profile);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'manual',
    resume_from_run_id TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task_id ON task_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_runs_resume_from ON task_runs(resume_from_run_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_started_at ON task_runs(started_at);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    name TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    cron TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    agent_profile TEXT,
    last_run_status TEXT,
    last_run_at TIMESTAMP,
    next_run_at TIMESTAMP,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_enabled ON scheduled_jobs(enabled);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_agent_profile ON scheduled_jobs(agent_profile);

PRAGMA user_version = 14;
