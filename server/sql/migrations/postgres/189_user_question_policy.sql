-- flowgate.v02gui.0021: user-owned question policy and resumable assistant draft.
CREATE TABLE IF NOT EXISTS user_question_policies (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    updated_at VARCHAR(64) NOT NULL
);
