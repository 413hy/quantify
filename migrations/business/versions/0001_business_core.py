"""Create independent business schemas and append-only foundations."""

from alembic import op

revision = "0001_business_core"
down_revision = None
branch_labels = ("business",)
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE SCHEMA reference")
    op.execute("CREATE SCHEMA market")
    op.execute("CREATE SCHEMA trading")
    op.execute("CREATE SCHEMA control")
    op.execute("CREATE SCHEMA research")
    op.execute(
        """
        CREATE TABLE control.audit_events (
            event_id varchar(64) PRIMARY KEY,
            occurred_at timestamptz NOT NULL,
            event_type varchar(128) NOT NULL,
            actor_id varchar(128) NOT NULL,
            correlation_id varchar(128) NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE trading.order_events (
            event_id varchar(64) PRIMARY KEY,
            intent_id varchar(64) NOT NULL,
            event_type varchar(96) NOT NULL,
            occurred_at timestamptz NOT NULL,
            exchange_event_time timestamptz,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            UNIQUE (intent_id, event_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.idempotency_keys (
            scope varchar(96) NOT NULL,
            idempotency_key varchar(160) NOT NULL,
            request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            result_ref varchar(160),
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz,
            PRIMARY KEY (scope, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.outbox (
            message_id varchar(64) PRIMARY KEY,
            deduplication_key varchar(160) NOT NULL UNIQUE,
            topic varchar(128) NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            status varchar(16) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','CLAIMED','PUBLISHED','DEAD')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            available_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE control.runtime_state (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            state varchar(32) NOT NULL DEFAULT 'RISK_LOCKED' CHECK (state IN (
                'BOOTSTRAP','RISK_LOCKED','RECONCILING','SHADOW','PAPER','TESTNET',
                'EXPERIMENTAL_LIVE','PAUSED_NEW_ENTRIES','EMERGENCY_FLATTENING','STOPPED'
            )),
            new_entries_allowed boolean NOT NULL DEFAULT false,
            reason_code varchar(128) NOT NULL DEFAULT 'STARTUP_EVIDENCE_MISSING',
            version bigint NOT NULL DEFAULT 1 CHECK (version >= 1),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (state <> 'RISK_LOCKED' OR new_entries_allowed = false)
        )
        """
    )
    op.execute("INSERT INTO control.runtime_state DEFAULT VALUES")
    op.execute(
        """
        CREATE FUNCTION control.reject_append_only_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table % cannot be mutated', TG_TABLE_NAME;
        END $$
        """
    )
    for table in ("control.audit_events", "trading.order_events"):
        trigger = table.replace(".", "_") + "_append_only"
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION control.reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA research CASCADE")
    op.execute("DROP SCHEMA control CASCADE")
    op.execute("DROP SCHEMA trading CASCADE")
    op.execute("DROP SCHEMA market CASCADE")
    op.execute("DROP SCHEMA reference CASCADE")
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
