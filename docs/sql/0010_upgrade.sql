BEGIN;

-- Running upgrade 0009 -> 0010

ALTER TABLE telegram_chats ADD COLUMN aliases_text TEXT;

ALTER TABLE telegram_chats ADD COLUMN tags_text TEXT;

ALTER TABLE telegram_chats ADD COLUMN brand VARCHAR(200);

ALTER TABLE telegram_chats ADD COLUMN last_used_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE message_dispatch_drafts (
    id UUID NOT NULL,
    bot_identity BIGINT NOT NULL,
    created_by_user_id UUID,
    created_by_telegram_id BIGINT,
    source_chat_id BIGINT NOT NULL,
    original_text TEXT NOT NULL,
    rendered_text TEXT NOT NULL,
    privacy_classification VARCHAR(30) DEFAULT 'PUBLIC_OPERATIONAL' NOT NULL,
    unresolved_phrases TEXT,
    status VARCHAR(20) DEFAULT 'CHOOSING' NOT NULL,
    version INTEGER DEFAULT '1' NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    dispatch_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatch_drafts PRIMARY KEY (id),
    CONSTRAINT fk_message_dispatch_drafts_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_message_dispatch_drafts_status ON message_dispatch_drafts (status);

CREATE INDEX ix_message_dispatch_drafts_open ON message_dispatch_drafts (bot_identity, source_chat_id, created_by_telegram_id, status);

CREATE TABLE message_dispatch_draft_recipients (
    id UUID NOT NULL,
    draft_id UUID NOT NULL,
    recipient_chat_row_id UUID NOT NULL,
    position INTEGER DEFAULT '0' NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    selected BOOLEAN DEFAULT false NOT NULL,
    selection_source VARCHAR(20) DEFAULT 'NAMED' NOT NULL,
    permitted BOOLEAN DEFAULT true NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatch_draft_recipients PRIMARY KEY (id),
    CONSTRAINT uq_dispatch_draft_recipients_draft_chat UNIQUE (draft_id, recipient_chat_row_id),
    CONSTRAINT fk_message_dispatch_draft_recipients_draft_id_message_d_3fcf FOREIGN KEY(draft_id) REFERENCES message_dispatch_drafts (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_dispatch_draft_recipients_recipient_chat_row_7a94 FOREIGN KEY(recipient_chat_row_id) REFERENCES telegram_chats (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_dispatch_draft_recipients_draft_id ON message_dispatch_draft_recipients (draft_id);

CREATE INDEX ix_dispatch_draft_recipients_draft ON message_dispatch_draft_recipients (draft_id, position);

CREATE TABLE message_dispatches (
    id UUID NOT NULL,
    bot_identity BIGINT NOT NULL,
    created_by_user_id UUID,
    created_by_telegram_id BIGINT,
    source_chat_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    privacy_classification VARCHAR(30) DEFAULT 'PUBLIC_OPERATIONAL' NOT NULL,
    status VARCHAR(20) DEFAULT 'QUEUED' NOT NULL,
    recipient_count INTEGER DEFAULT '0' NOT NULL,
    delivered_count INTEGER DEFAULT '0' NOT NULL,
    retrying_count INTEGER DEFAULT '0' NOT NULL,
    failed_count INTEGER DEFAULT '0' NOT NULL,
    total_parts INTEGER DEFAULT '1' NOT NULL,
    version INTEGER DEFAULT '1' NOT NULL,
    confirmed_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    summary_sent_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatches PRIMARY KEY (id),
    CONSTRAINT fk_message_dispatches_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_message_dispatches_status ON message_dispatches (status);

CREATE INDEX ix_message_dispatches_status_created ON message_dispatches (status, created_at);

CREATE TABLE message_dispatch_parts (
    id UUID NOT NULL,
    dispatch_id UUID NOT NULL,
    part_number INTEGER NOT NULL,
    total_parts INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatch_parts PRIMARY KEY (id),
    CONSTRAINT uq_dispatch_parts_dispatch_number UNIQUE (dispatch_id, part_number),
    CONSTRAINT fk_message_dispatch_parts_dispatch_id_message_dispatches FOREIGN KEY(dispatch_id) REFERENCES message_dispatches (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_dispatch_parts_dispatch_id ON message_dispatch_parts (dispatch_id);

CREATE TABLE message_dispatch_recipients (
    id UUID NOT NULL,
    dispatch_id UUID NOT NULL,
    telegram_chat_row_id UUID NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    destination_display_name VARCHAR(200) NOT NULL,
    position INTEGER DEFAULT '0' NOT NULL,
    status VARCHAR(20) DEFAULT 'QUEUED' NOT NULL,
    delivered_parts INTEGER DEFAULT '0' NOT NULL,
    failed_parts INTEGER DEFAULT '0' NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE,
    failed_at TIMESTAMP WITH TIME ZONE,
    failure_category VARCHAR(40) DEFAULT 'NONE' NOT NULL,
    attempt_version INTEGER DEFAULT '1' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatch_recipients PRIMARY KEY (id),
    CONSTRAINT uq_dispatch_recipients_dispatch_chat UNIQUE (dispatch_id, telegram_chat_row_id),
    CONSTRAINT fk_message_dispatch_recipients_dispatch_id_message_dispatches FOREIGN KEY(dispatch_id) REFERENCES message_dispatches (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_dispatch_recipients_telegram_chat_row_id_tel_7050 FOREIGN KEY(telegram_chat_row_id) REFERENCES telegram_chats (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_dispatch_recipients_dispatch_id ON message_dispatch_recipients (dispatch_id);

CREATE INDEX ix_dispatch_recipients_status ON message_dispatch_recipients (dispatch_id, status);

CREATE TABLE message_dispatch_recipient_parts (
    id UUID NOT NULL,
    recipient_id UUID NOT NULL,
    dispatch_part_id UUID NOT NULL,
    part_number INTEGER DEFAULT '1' NOT NULL,
    outbound_message_id UUID,
    status VARCHAR(20) DEFAULT 'QUEUED' NOT NULL,
    delivered_at TIMESTAMP WITH TIME ZONE,
    failed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_message_dispatch_recipient_parts PRIMARY KEY (id),
    CONSTRAINT uq_dispatch_recipient_parts_recipient_part UNIQUE (recipient_id, dispatch_part_id),
    CONSTRAINT fk_message_dispatch_recipient_parts_recipient_id_messag_64a8 FOREIGN KEY(recipient_id) REFERENCES message_dispatch_recipients (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_dispatch_recipient_parts_dispatch_part_id_me_0c9b FOREIGN KEY(dispatch_part_id) REFERENCES message_dispatch_parts (id) ON DELETE CASCADE,
    CONSTRAINT fk_message_dispatch_recipient_parts_outbound_message_id_9244 FOREIGN KEY(outbound_message_id) REFERENCES outbound_messages (id) ON DELETE SET NULL
);

CREATE INDEX ix_message_dispatch_recipient_parts_recipient_id ON message_dispatch_recipient_parts (recipient_id);

CREATE INDEX ix_dispatch_recipient_parts_outbound ON message_dispatch_recipient_parts (outbound_message_id);

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';

COMMIT;
