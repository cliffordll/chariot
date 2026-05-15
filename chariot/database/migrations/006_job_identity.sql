ALTER TABLE scheduled_jobs ADD COLUMN id TEXT;
CREATE UNIQUE INDEX idx_scheduled_jobs_id ON scheduled_jobs(id);

ALTER TABLE job_runs ADD COLUMN job_id TEXT;
CREATE INDEX idx_job_runs_job_id ON job_runs(job_id);

PRAGMA user_version = 6;
