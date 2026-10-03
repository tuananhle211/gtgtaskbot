BEGIN;

-- Running downgrade 0011 -> 0010

ALTER TABLE message_dispatch_drafts ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatch_drafts ALTER COLUMN updated_at DROP DEFAULT;

ALTER TABLE message_dispatch_draft_recipients ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatch_draft_recipients ALTER COLUMN updated_at DROP DEFAULT;

ALTER TABLE message_dispatches ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatches ALTER COLUMN updated_at DROP DEFAULT;

ALTER TABLE message_dispatch_recipients ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatch_recipients ALTER COLUMN updated_at DROP DEFAULT;

ALTER TABLE message_dispatch_parts ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatch_recipient_parts ALTER COLUMN created_at DROP DEFAULT;

ALTER TABLE message_dispatch_recipient_parts ALTER COLUMN updated_at DROP DEFAULT;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0011';

COMMIT;
