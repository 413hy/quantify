"""Add append-only Reserve and Consume decision audit journals."""

from alembic import op

revision = "0008_decision_audit"
down_revision = "0007_header_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rate_control.reservation_decisions (
            decision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_id varchar(128) NOT NULL UNIQUE,
            request_message_id varchar(128) NOT NULL,
            request_key varchar(160) NOT NULL,
            decision varchar(16) NOT NULL CHECK (decision IN ('GRANTED','DENIED')),
            reason_code varchar(64) NOT NULL,
            permit_id varchar(64) REFERENCES rate_control.permits(permit_id),
            caller_service varchar(96) NOT NULL,
            caller_instance_id varchar(128) NOT NULL,
            endpoint_authority varchar(96) NOT NULL,
            endpoint_id varchar(128) NOT NULL,
            derived_operation_class varchar(48),
            endpoint_catalog_hash char(64) NOT NULL
              CHECK (endpoint_catalog_hash ~ '^[0-9a-f]{64}$'),
            operation_facts_hash char(64) NOT NULL
              CHECK (operation_facts_hash ~ '^[0-9a-f]{64}$'),
            capability_payload_hash char(64) NOT NULL
              CHECK (capability_payload_hash ~ '^[0-9a-f]{64}$'),
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch >= 1),
            peer_pid integer NOT NULL CHECK (peer_pid > 0),
            peer_uid integer NOT NULL CHECK (peer_uid >= 0),
            peer_gid integer NOT NULL CHECK (peer_gid >= 0),
            occurred_at timestamptz NOT NULL,
            CHECK ((decision = 'GRANTED') = (permit_id IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.consume_decisions (
            decision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_id varchar(128) NOT NULL UNIQUE,
            request_message_id varchar(128) NOT NULL,
            permit_id varchar(64) NOT NULL,
            decision varchar(24) NOT NULL
              CHECK (decision IN ('CONSUME_GRANTED','CONSUME_DENIED')),
            reason_code varchar(64) NOT NULL,
            gateway_instance_id varchar(128) NOT NULL,
            canonical_request_hash char(64) NOT NULL
              CHECK (canonical_request_hash ~ '^[0-9a-f]{64}$'),
            parameter_hash char(64) NOT NULL
              CHECK (parameter_hash ~ '^[0-9a-f]{64}$'),
            wire_bytes_hash char(64) NOT NULL
              CHECK (wire_bytes_hash ~ '^[0-9a-f]{64}$'),
            operation_facts_hash char(64) NOT NULL
              CHECK (operation_facts_hash ~ '^[0-9a-f]{64}$'),
            capability_payload_hash char(64) NOT NULL
              CHECK (capability_payload_hash ~ '^[0-9a-f]{64}$'),
            request_document_hash char(64) NOT NULL
              CHECK (request_document_hash ~ '^[0-9a-f]{64}$'),
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch >= 1),
            send_deadline timestamptz,
            peer_pid integer NOT NULL CHECK (peer_pid > 0),
            peer_uid integer NOT NULL CHECK (peer_uid >= 0),
            peer_gid integer NOT NULL CHECK (peer_gid >= 0),
            occurred_at timestamptz NOT NULL,
            CHECK ((decision = 'CONSUME_GRANTED') = (send_deadline IS NOT NULL))
        )
        """
    )
    for table in ("reservation_decisions", "consume_decisions"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
            f"ON rate_control.{table} FOR EACH ROW EXECUTE FUNCTION "
            "rate_control.reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE rate_control.consume_decisions")
    op.execute("DROP TABLE rate_control.reservation_decisions")
