CREATE TABLE IF NOT EXISTS env_variables (
  var_id VARCHAR(191) PRIMARY KEY,
  kind VARCHAR(191) NOT NULL CHECK (kind IN ('system', 'user')),
  name VARCHAR(191) NOT NULL,
  value TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (kind, name)
);
CREATE INDEX IF NOT EXISTS idx_env_vars_kind ON env_variables(kind);
CREATE TABLE IF NOT EXISTS commands (
  command_id VARCHAR(191) PRIMARY KEY,
  kind VARCHAR(191) NOT NULL CHECK (kind IN ('system', 'user')),
  name VARCHAR(191) NOT NULL,
  template TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (kind, name)
);
CREATE INDEX IF NOT EXISTS idx_commands_kind ON commands(kind);