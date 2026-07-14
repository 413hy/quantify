"""Add append-only control, notification, incident, and backup evidence."""

from alembic import op

revision = "0004_operations"
down_revision = "0003_risk_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE control.command_requests (
            command_id varchar(64) PRIMARY KEY,
            command_type varchar(64) NOT NULL,
            actor_id varchar(120) NOT NULL,
            source varchar(32) NOT NULL,
            idempotency_key varchar(128) NOT NULL UNIQUE,
            request_payload jsonb NOT NULL,
            request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            requested_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz NOT NULL,
            CHECK (expires_at > requested_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.command_events (
            event_id varchar(64) PRIMARY KEY,
            command_id varchar(64) NOT NULL REFERENCES control.command_requests(command_id),
            state varchar(16) NOT NULL CHECK (state IN (
                'PENDING','ACCEPTED','REJECTED','COMPLETED','FAILED'
            )),
            reason_codes jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL,
            UNIQUE (command_id, event_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.flatten_challenges (
            challenge_id varchar(64) PRIMARY KEY,
            actor_id varchar(120) NOT NULL,
            positions_digest char(64) NOT NULL CHECK (positions_digest ~ '^[0-9a-f]{64}$'),
            phrase_hash char(64) NOT NULL CHECK (phrase_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.flatten_challenge_consumptions (
            consumption_id varchar(64) PRIMARY KEY,
            challenge_id varchar(64) NOT NULL UNIQUE REFERENCES control.flatten_challenges(challenge_id),
            command_id varchar(64) NOT NULL UNIQUE REFERENCES control.command_requests(command_id),
            consumed_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.incident_events (
            event_id varchar(64) PRIMARY KEY,
            incident_id varchar(64) NOT NULL,
            severity varchar(8) NOT NULL CHECK (severity IN ('P0','P1','P2','P3')),
            state varchar(16) NOT NULL CHECK (state IN ('OPEN','MITIGATING','RESOLVED')),
            event_type varchar(128) NOT NULL,
            runbook varchar(256) NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL,
            UNIQUE (incident_id, event_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.notification_deliveries (
            delivery_id varchar(64) PRIMARY KEY,
            deduplication_key varchar(160) NOT NULL,
            channel varchar(32) NOT NULL CHECK (channel IN ('TELEGRAM','FEISHU','EXTERNAL_HEARTBEAT')),
            message_hash char(64) NOT NULL CHECK (message_hash ~ '^[0-9a-f]{64}$'),
            state varchar(16) NOT NULL CHECK (state IN ('SENT','FAILED','RATE_LIMITED','DEDUPLICATED')),
            occurred_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.backup_manifests (
            backup_id varchar(64) PRIMARY KEY,
            backup_type varchar(32) NOT NULL CHECK (backup_type IN ('BASE','WAL','RESTORE_DRILL')),
            manifest jsonb NOT NULL,
            manifest_hash char(64) NOT NULL UNIQUE CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
            remote_receipt_hash char(64),
            verified boolean NOT NULL,
            created_at timestamptz NOT NULL,
            CHECK (
                remote_receipt_hash IS NULL
                OR remote_receipt_hash ~ '^[0-9a-f]{64}$'
            )
        )
        """
    )
    for table in (
        "control.command_requests",
        "control.command_events",
        "control.flatten_challenges",
        "control.flatten_challenge_consumptions",
        "control.incident_events",
        "control.notification_deliveries",
        "control.backup_manifests",
    ):
        trigger = table.replace(".", "_") + "_append_only"
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION control.reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE control.backup_manifests")
    op.execute("DROP TABLE control.notification_deliveries")
    op.execute("DROP TABLE control.incident_events")
    op.execute("DROP TABLE control.flatten_challenge_consumptions")
    op.execute("DROP TABLE control.flatten_challenges")
    op.execute("DROP TABLE control.command_events")
    op.execute("DROP TABLE control.command_requests")
