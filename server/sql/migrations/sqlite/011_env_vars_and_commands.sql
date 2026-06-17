CREATE TABLE IF NOT EXISTS env_variables (
  var_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('system', 'user')),
  name TEXT NOT NULL,
  value TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (kind, name)
);

CREATE INDEX IF NOT EXISTS idx_env_vars_kind ON env_variables(kind);

CREATE TABLE IF NOT EXISTS commands (
  command_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('system', 'user')),
  name TEXT NOT NULL,
  template TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (kind, name)
);

CREATE INDEX IF NOT EXISTS idx_commands_kind ON commands(kind);
