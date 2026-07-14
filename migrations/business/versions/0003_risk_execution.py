"""Add immutable risk, intent, account, position, and protection evidence."""

from alembic import op

revision = "0003_risk_execution"
down_revision = "0002_market_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trading.risk_decisions (
            decision_id varchar(64) PRIMARY KEY,
            signal_id varchar(64),
            position_id varchar(64),
            decision_kind varchar(32) NOT NULL CHECK (decision_kind IN (
                'ENTRY','STRATEGY_EXIT','PROTECTIVE_EXIT','RISK_EXIT',
                'OPERATOR_FLATTEN','RECONCILIATION_REPAIR'
            )),
            approved boolean NOT NULL,
            quantity numeric(38,18) NOT NULL CHECK (quantity >= 0),
            configured_limits jsonb NOT NULL,
            effective_limits jsonb NOT NULL,
            current_risk jsonb NOT NULL,
            decision_payload jsonb NOT NULL,
            decision_hash char(64) NOT NULL CHECK (decision_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE trading.risk_reservation_events (
            reservation_event_id varchar(64) PRIMARY KEY,
            reservation_id varchar(64) NOT NULL,
            decision_id varchar(64) NOT NULL REFERENCES trading.risk_decisions(decision_id),
            event_type varchar(16) NOT NULL CHECK (event_type IN ('RESERVE','ADJUST','RELEASE')),
            amount numeric(38,18) NOT NULL CHECK (amount >= 0),
            symbol varchar(24) NOT NULL,
            cluster_id varchar(64) NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE trading.order_intents (
            intent_id varchar(64) PRIMARY KEY,
            decision_id varchar(64) NOT NULL UNIQUE REFERENCES trading.risk_decisions(decision_id),
            idempotency_key varchar(160) NOT NULL UNIQUE,
            transport varchar(16) NOT NULL CHECK (transport IN ('STANDARD','ALGO')),
            client_order_id varchar(64) UNIQUE,
            client_algo_id varchar(64) UNIQUE,
            symbol varchar(24) NOT NULL,
            side varchar(8) NOT NULL CHECK (side IN ('BUY','SELL')),
            order_role varchar(32) NOT NULL,
            quantity numeric(38,18) NOT NULL CHECK (quantity > 0),
            reduce_only boolean NOT NULL,
            close_position boolean NOT NULL,
            expires_at timestamptz NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL,
            CHECK (
                (transport='STANDARD' AND client_order_id IS NOT NULL AND client_algo_id IS NULL)
                OR
                (transport='ALGO' AND client_algo_id IS NOT NULL AND client_order_id IS NULL)
            )
        )
        """
    )
    op.execute(
        "ALTER TABLE trading.order_events ADD CONSTRAINT order_events_intent_fk "
        "FOREIGN KEY (intent_id) REFERENCES trading.order_intents(intent_id)"
    )
    op.execute(
        """
        CREATE TABLE trading.account_snapshots (
            snapshot_id varchar(64) PRIMARY KEY,
            observed_at timestamptz NOT NULL,
            source varchar(16) NOT NULL CHECK (source IN ('USER_STREAM','REST','RECONCILIATION')),
            one_way_mode boolean NOT NULL,
            cross_margin boolean NOT NULL,
            equity numeric(38,18) NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE trading.position_snapshots (
            snapshot_id varchar(64) PRIMARY KEY,
            account_snapshot_id varchar(64) NOT NULL REFERENCES trading.account_snapshots(snapshot_id),
            position_id varchar(64),
            symbol varchar(24) NOT NULL,
            quantity numeric(38,18) NOT NULL,
            protected_quantity numeric(38,18) NOT NULL CHECK (protected_quantity >= 0),
            strategy_version varchar(128),
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            observed_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE trading.protection_observations (
            observation_id varchar(64) PRIMARY KEY,
            position_id varchar(64) NOT NULL,
            intent_id varchar(64) REFERENCES trading.order_intents(intent_id),
            symbol varchar(24) NOT NULL,
            position_quantity numeric(38,18) NOT NULL,
            protected_quantity numeric(38,18) NOT NULL CHECK (protected_quantity >= 0),
            exchange_confirmed boolean NOT NULL,
            healthy boolean NOT NULL,
            evidence jsonb NOT NULL,
            evidence_hash char(64) NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
            observed_at timestamptz NOT NULL
        )
        """
    )
    for table in (
        "trading.risk_decisions",
        "trading.risk_reservation_events",
        "trading.order_intents",
        "trading.account_snapshots",
        "trading.position_snapshots",
        "trading.protection_observations",
    ):
        trigger = table.replace(".", "_") + "_append_only"
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION control.reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE trading.protection_observations")
    op.execute("DROP TABLE trading.position_snapshots")
    op.execute("DROP TABLE trading.account_snapshots")
    op.execute("ALTER TABLE trading.order_events DROP CONSTRAINT order_events_intent_fk")
    op.execute("DROP TABLE trading.order_intents")
    op.execute("DROP TABLE trading.risk_reservation_events")
    op.execute("DROP TABLE trading.risk_decisions")
