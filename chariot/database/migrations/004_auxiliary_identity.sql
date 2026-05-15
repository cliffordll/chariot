ALTER TABLE auxiliary_clients ADD COLUMN id TEXT;
CREATE UNIQUE INDEX idx_auxiliary_clients_id ON auxiliary_clients(id);

PRAGMA user_version = 4;
