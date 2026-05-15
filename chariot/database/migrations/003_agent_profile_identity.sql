ALTER TABLE agent_profiles ADD COLUMN id TEXT;
CREATE UNIQUE INDEX idx_agent_profiles_id ON agent_profiles(id);

ALTER TABLE tasks ADD COLUMN agent_profile_id TEXT;
CREATE INDEX idx_tasks_agent_profile_id ON tasks(agent_profile_id);

ALTER TABLE scheduled_jobs ADD COLUMN agent_profile_id TEXT;
CREATE INDEX idx_scheduled_jobs_agent_profile_id ON scheduled_jobs(agent_profile_id);

PRAGMA user_version = 3;
