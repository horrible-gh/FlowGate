-- flowgate.v02gui.0021: user-owned question policy and resumable assistant draft.
CREATE TABLE IF NOT EXISTS user_question_policies (
    user_id VARCHAR(191) PRIMARY KEY,
    body MEDIUMTEXT NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
