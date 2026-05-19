ALTER TABLE toolsets ADD COLUMN id TEXT;
CREATE UNIQUE INDEX idx_toolsets_id ON toolsets(id);

ALTER TABLE toolset_members ADD COLUMN toolset_id TEXT;
CREATE INDEX idx_toolset_members_toolset_id ON toolset_members(toolset_id);

ALTER TABLE agent_profiles ADD COLUMN toolset_id TEXT;
CREATE INDEX idx_agent_profiles_toolset_id ON agent_profiles(toolset_id);

PRAGMA user_version = 5;
