-- Flow-owned images: deliberately no document/turn foreign keys.
CREATE TABLE message_images (
    image_id VARCHAR(64) PRIMARY KEY,
    flow_id VARCHAR(200) NOT NULL,
    project_id VARCHAR(100) NOT NULL,
    uploaded_by VARCHAR(100) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    media_type VARCHAR(32) NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('temp', 'linked')),
    created_at VARCHAR(40) NOT NULL,
    linked_at VARCHAR(40)
);
CREATE INDEX ix_message_images_expiry ON message_images(state, created_at);
CREATE INDEX ix_message_images_flow ON message_images(flow_id);
CREATE TABLE message_image_links (
    flow_id VARCHAR(200) NOT NULL,
    conversation_id VARCHAR(200) NOT NULL,
    seq INTEGER NOT NULL,
    image_id VARCHAR(64) NOT NULL UNIQUE,
    position INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, seq, position)
);
