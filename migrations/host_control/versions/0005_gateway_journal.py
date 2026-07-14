"""Persist idempotent gateway outcomes and authenticated observations."""

from alembic import op

revision = "0005_gateway_journal"
down_revision = "0004_multiclass_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rate_control.gateway_message_receipts (
            message_id varchar(128) PRIMARY KEY,
            message_type varchar(48) NOT NULL CHECK (message_type IN (
              'SendOutcome','HeaderObservation','ConnectionStateObservation',
              'ServerTimeObservation','ExchangeRateLimitObservation'
            )),
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.send_outcomes (
            outcome_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_id varchar(128) NOT NULL UNIQUE
              REFERENCES rate_control.gateway_message_receipts(message_id),
            permit_id varchar(64) NOT NULL UNIQUE REFERENCES rate_control.permits(permit_id),
            gateway_instance_id varchar(128) NOT NULL,
            outcome varchar(32) NOT NULL CHECK (
              outcome IN ('NOT_SENT','SENT_DEFINITE_RESULT','SENT_UNKNOWN')
            ),
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        ALTER TABLE rate_control.observations
          ADD COLUMN permit_id varchar(64) REFERENCES rate_control.permits(permit_id),
          ADD COLUMN gateway_instance_id varchar(128),
          ADD COLUMN fencing_epoch bigint CHECK (fencing_epoch IS NULL OR fencing_epoch >= 1)
        """
    )
    op.execute(
        """
        INSERT INTO rate_control.gateway_message_receipts(
          message_id,message_type,payload_hash,recorded_at
        ) SELECT message_id,observation_type,payload_hash,recorded_at
            FROM rate_control.observations
        """
    )
    op.execute(
        """
        ALTER TABLE rate_control.observations
          ADD CONSTRAINT observation_receipt_fk FOREIGN KEY(message_id)
            REFERENCES rate_control.gateway_message_receipts(message_id)
        """
    )
    for table in ("gateway_message_receipts", "send_outcomes"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
            f"ON rate_control.{table} FOR EACH ROW EXECUTE FUNCTION "
            "rate_control.reject_append_only_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION rate_control.record_gateway_message(
            requested_payload jsonb,
            requested_payload_hash char(64)
        ) RETURNS TABLE(decision varchar, reason_code varchar)
        LANGUAGE plpgsql AS $$
        DECLARE
            requested_message_id varchar := requested_payload->>'message_id';
            requested_message_type varchar := requested_payload->>'message_type';
            requested_permit_id varchar;
            requested_gateway_instance varchar := requested_payload->>'caller_instance_id';
            requested_fencing_epoch bigint;
            requested_occurred_at timestamptz;
            requested_endpoint_authority varchar;
            existing_receipt rate_control.gateway_message_receipts%ROWTYPE;
            permit_row rate_control.permits%ROWTYPE;
            state_row rate_control.fencing_state%ROWTYPE;
        BEGIN
            IF jsonb_typeof(requested_payload) <> 'object'
               OR requested_message_id IS NULL OR requested_gateway_instance IS NULL
               OR requested_message_type NOT IN (
                 'SendOutcome','HeaderObservation','ConnectionStateObservation',
                 'ServerTimeObservation','ExchangeRateLimitObservation'
               ) THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_GATEWAY_EVENT_INVALID'::varchar;
                RETURN;
            END IF;
            BEGIN
                requested_fencing_epoch := (requested_payload->>'fencing_epoch')::bigint;
                requested_occurred_at := (requested_payload->>'occurred_at')::timestamptz;
            EXCEPTION WHEN OTHERS THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_GATEWAY_EVENT_INVALID'::varchar;
                RETURN;
            END;
            IF requested_fencing_epoch < 1 OR requested_occurred_at IS NULL THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_GATEWAY_EVENT_INVALID'::varchar;
                RETURN;
            END IF;

            SELECT * INTO existing_receipt
              FROM rate_control.gateway_message_receipts
             WHERE message_id = requested_message_id FOR UPDATE;
            IF FOUND THEN
                IF existing_receipt.message_type = requested_message_type
                   AND existing_receipt.payload_hash = requested_payload_hash THEN
                    RETURN QUERY SELECT 'RECORDED'::varchar,
                      'RATE_GATEWAY_EVENT_IDEMPOTENT'::varchar;
                ELSE
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_GATEWAY_EVENT_REPLAYED'::varchar;
                END IF;
                RETURN;
            END IF;

            IF requested_message_type = 'ConnectionStateObservation' THEN
                requested_permit_id := requested_payload->>'related_permit_id';
            ELSE
                requested_permit_id := requested_payload->>'permit_id';
            END IF;
            requested_endpoint_authority := requested_payload->>'endpoint_authority';

            IF requested_permit_id IS NOT NULL THEN
                SELECT * INTO permit_row FROM rate_control.permits
                 WHERE permit_id = requested_permit_id FOR UPDATE;
                IF NOT FOUND OR permit_row.state <> 'CONSUMED'
                   OR permit_row.gateway_instance_id <> requested_gateway_instance
                   OR permit_row.fencing_epoch <> requested_fencing_epoch THEN
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_GATEWAY_EVENT_BINDING_MISMATCH'::varchar;
                    RETURN;
                END IF;
                IF requested_message_type = 'SendOutcome'
                   AND permit_row.canonical_request_hash <>
                       requested_payload->>'canonical_request_hash' THEN
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_GATEWAY_EVENT_BINDING_MISMATCH'::varchar;
                    RETURN;
                END IF;
                IF requested_endpoint_authority IS NOT NULL
                   AND permit_row.endpoint_authority <> requested_endpoint_authority THEN
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_GATEWAY_EVENT_BINDING_MISMATCH'::varchar;
                    RETURN;
                END IF;
            ELSE
                SELECT * INTO state_row FROM rate_control.fencing_state
                 WHERE singleton = true FOR UPDATE;
                IF requested_message_type <> 'ConnectionStateObservation'
                   OR NOT FOUND OR state_row.epoch <> requested_fencing_epoch
                   OR state_row.lease_expires_at IS NULL
                   OR state_row.lease_expires_at <= clock_timestamp() THEN
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_FENCING_STALE'::varchar;
                    RETURN;
                END IF;
            END IF;

            INSERT INTO rate_control.gateway_message_receipts(
              message_id,message_type,payload_hash
            ) VALUES (
              requested_message_id,requested_message_type,requested_payload_hash
            );
            IF requested_message_type = 'SendOutcome' THEN
                INSERT INTO rate_control.send_outcomes(
                  message_id,permit_id,gateway_instance_id,outcome,payload,payload_hash,
                  occurred_at
                ) VALUES (
                  requested_message_id,requested_permit_id,requested_gateway_instance,
                  requested_payload->>'outcome',requested_payload,requested_payload_hash,
                  requested_occurred_at
                );
            ELSE
                INSERT INTO rate_control.observations(
                  message_id,observation_type,endpoint_authority,payload,payload_hash,
                  occurred_at,permit_id,gateway_instance_id,fencing_epoch
                ) VALUES (
                  requested_message_id,requested_message_type,
                  COALESCE(requested_endpoint_authority, permit_row.endpoint_authority),
                  requested_payload,requested_payload_hash,requested_occurred_at,
                  requested_permit_id,requested_gateway_instance,requested_fencing_epoch
                );
            END IF;
            RETURN QUERY SELECT 'RECORDED'::varchar, 'RATE_GATEWAY_EVENT_RECORDED'::varchar;
        EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
            RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_GATEWAY_EVENT_CONFLICT'::varchar;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION rate_control.record_gateway_message(jsonb,char)")
    op.execute(
        "ALTER TABLE rate_control.observations "
        "DROP CONSTRAINT observation_receipt_fk, DROP COLUMN fencing_epoch, "
        "DROP COLUMN gateway_instance_id, DROP COLUMN permit_id"
    )
    op.execute("DROP TABLE rate_control.send_outcomes")
    op.execute("DROP TABLE rate_control.gateway_message_receipts")
