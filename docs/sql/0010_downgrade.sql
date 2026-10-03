BEGIN;

-- Running downgrade 0010 -> 0009

DROP INDEX ix_dispatch_recipient_parts_outbound;

DROP INDEX ix_message_dispatch_recipient_parts_recipient_id;

DROP TABLE message_dispatch_recipient_parts;

DROP INDEX ix_dispatch_recipients_status;

DROP INDEX ix_message_dispatch_recipients_dispatch_id;

DROP TABLE message_dispatch_recipients;

DROP INDEX ix_message_dispatch_parts_dispatch_id;

DROP TABLE message_dispatch_parts;

DROP INDEX ix_message_dispatches_status_created;

DROP INDEX ix_message_dispatches_status;

DROP TABLE message_dispatches;

DROP INDEX ix_dispatch_draft_recipients_draft;

DROP INDEX ix_message_dispatch_draft_recipients_draft_id;

DROP TABLE message_dispatch_draft_recipients;

DROP INDEX ix_message_dispatch_drafts_open;

DROP INDEX ix_message_dispatch_drafts_status;

DROP TABLE message_dispatch_drafts;

ALTER TABLE telegram_chats DROP COLUMN last_used_at;

ALTER TABLE telegram_chats DROP COLUMN brand;

ALTER TABLE telegram_chats DROP COLUMN tags_text;

ALTER TABLE telegram_chats DROP COLUMN aliases_text;

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0010';

COMMIT;
