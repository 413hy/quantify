"""Require an active PostgreSQL epoch lease for Reserve and Consume."""

from alembic import op

revision = "0003_fencing_lease"
down_revision = "0002_atomic_reservation"
branch_labels = None
depends_on = None


RESERVE_ARGUMENTS = (
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,"
    "char,char,char,char,char,char,char,varchar,bigint,timestamptz"
)
CONSUME_ARGUMENTS = "varchar,char,char,char,char,char,char,bigint,varchar"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rate_control.fencing_state
          ADD COLUMN lease_acquired_at timestamptz,
          ADD COLUMN lease_renewed_at timestamptz,
          ADD COLUMN lease_expires_at timestamptz,
          ADD CONSTRAINT fencing_lease_shape CHECK (
            (lease_acquired_at IS NULL AND lease_renewed_at IS NULL AND lease_expires_at IS NULL)
            OR
            (lease_acquired_at IS NOT NULL AND lease_renewed_at IS NOT NULL
             AND lease_expires_at > lease_renewed_at
             AND lease_renewed_at >= lease_acquired_at)
          )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.fencing_lease_events (
            event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            allocator_instance_id varchar(128) NOT NULL,
            previous_epoch bigint NOT NULL CHECK (previous_epoch >= 1),
            resulting_epoch bigint NOT NULL CHECK (resulting_epoch >= previous_epoch),
            event_type varchar(16) NOT NULL CHECK (event_type IN ('ACQUIRED','RENEWED','DENIED')),
            reason_code varchar(48) NOT NULL,
            lease_expires_at timestamptz,
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER fencing_lease_events_append_only
          BEFORE UPDATE OR DELETE ON rate_control.fencing_lease_events
          FOR EACH ROW EXECUTE FUNCTION rate_control.reject_append_only_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.acquire_fencing_lease(
            requested_allocator_instance_id varchar,
            expected_previous_epoch bigint,
            requested_ttl_seconds integer
        ) RETURNS TABLE(
            decision varchar,
            reason_code varchar,
            fencing_epoch bigint,
            lease_expires_at timestamptz
        )
        LANGUAGE plpgsql AS $$
        DECLARE
            state_row rate_control.fencing_state%ROWTYPE;
            now_at timestamptz := clock_timestamp();
            new_expiry timestamptz;
            new_epoch bigint;
        BEGIN
            SELECT * INTO state_row FROM rate_control.fencing_state
              WHERE singleton = true FOR UPDATE;
            IF NOT FOUND OR requested_allocator_instance_id IS NULL
               OR requested_allocator_instance_id = ''
               OR requested_allocator_instance_id = 'UNINITIALIZED'
               OR requested_ttl_seconds < 1 OR requested_ttl_seconds > 300 THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'FENCING_REQUEST_INVALID'::varchar,
                  COALESCE(state_row.epoch, 0), NULL::timestamptz;
                RETURN;
            END IF;
            IF expected_previous_epoch <> state_row.epoch THEN
                INSERT INTO rate_control.fencing_lease_events(
                  allocator_instance_id,previous_epoch,resulting_epoch,event_type,reason_code
                ) VALUES (
                  requested_allocator_instance_id,state_row.epoch,state_row.epoch,'DENIED',
                  'FENCING_EPOCH_STALE'
                );
                RETURN QUERY SELECT 'DENIED'::varchar, 'FENCING_EPOCH_STALE'::varchar,
                  state_row.epoch, state_row.lease_expires_at;
                RETURN;
            END IF;
            new_expiry := now_at + make_interval(secs => requested_ttl_seconds);
            IF state_row.lease_expires_at > now_at
               AND state_row.allocator_instance_id <> requested_allocator_instance_id THEN
                INSERT INTO rate_control.fencing_lease_events(
                  allocator_instance_id,previous_epoch,resulting_epoch,event_type,reason_code,
                  lease_expires_at
                ) VALUES (
                  requested_allocator_instance_id,state_row.epoch,state_row.epoch,'DENIED',
                  'FENCING_LEASE_HELD',state_row.lease_expires_at
                );
                RETURN QUERY SELECT 'DENIED'::varchar, 'FENCING_LEASE_HELD'::varchar,
                  state_row.epoch, state_row.lease_expires_at;
                RETURN;
            END IF;
            IF state_row.lease_expires_at > now_at
               AND state_row.allocator_instance_id = requested_allocator_instance_id THEN
                UPDATE rate_control.fencing_state
                   SET lease_renewed_at = now_at, lease_expires_at = new_expiry,
                       updated_at = now_at
                 WHERE singleton = true;
                INSERT INTO rate_control.fencing_lease_events(
                  allocator_instance_id,previous_epoch,resulting_epoch,event_type,reason_code,
                  lease_expires_at
                ) VALUES (
                  requested_allocator_instance_id,state_row.epoch,state_row.epoch,'RENEWED',
                  'FENCING_LEASE_RENEWED',new_expiry
                );
                RETURN QUERY SELECT 'GRANTED'::varchar, 'FENCING_LEASE_RENEWED'::varchar,
                  state_row.epoch, new_expiry;
                RETURN;
            END IF;
            new_epoch := state_row.epoch + 1;
            UPDATE rate_control.fencing_state
               SET epoch = new_epoch,
                   allocator_instance_id = requested_allocator_instance_id,
                   lease_acquired_at = now_at,
                   lease_renewed_at = now_at,
                   lease_expires_at = new_expiry,
                   updated_at = now_at
             WHERE singleton = true;
            INSERT INTO rate_control.fencing_lease_events(
              allocator_instance_id,previous_epoch,resulting_epoch,event_type,reason_code,
              lease_expires_at
            ) VALUES (
              requested_allocator_instance_id,state_row.epoch,new_epoch,'ACQUIRED',
              'FENCING_LEASE_ACQUIRED',new_expiry
            );
            RETURN QUERY SELECT 'GRANTED'::varchar, 'FENCING_LEASE_ACQUIRED'::varchar,
              new_epoch, new_expiry;
        END $$
        """
    )

    op.execute(
        f"ALTER FUNCTION rate_control.reserve_permit({RESERVE_ARGUMENTS}) "
        "RENAME TO reserve_permit_under_active_lease"
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.reserve_permit(
            requested_permit_id varchar,
            requested_request_key varchar,
            requested_subject_service varchar,
            requested_subject_instance varchar,
            requested_environment varchar,
            requested_endpoint_authority varchar,
            requested_endpoint_id varchar,
            requested_catalog_hash char(64),
            requested_canonical_hash char(64),
            requested_parameter_hash char(64),
            requested_wire_hash char(64),
            requested_operation_facts_hash char(64),
            requested_capability_hash char(64),
            requested_document_hash char(64),
            requested_capability_nonce varchar,
            requested_fencing_epoch bigint,
            requested_expires_at timestamptz
        ) RETURNS TABLE(
            decision varchar,
            reason_code varchar,
            permit_id varchar,
            derived_operation_class varchar,
            fencing_epoch bigint,
            expires_at timestamptz
        )
        LANGUAGE plpgsql AS $$
        DECLARE
            state_row rate_control.fencing_state%ROWTYPE;
        BEGIN
            SELECT * INTO state_row FROM rate_control.fencing_state
              WHERE singleton = true FOR UPDATE;
            IF NOT FOUND OR state_row.epoch <> requested_fencing_epoch
               OR state_row.lease_expires_at IS NULL
               OR state_row.lease_expires_at <= clock_timestamp() THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_FENCING_STALE'::varchar,
                  NULL::varchar, NULL::varchar, COALESCE(state_row.epoch, 0), NULL::timestamptz;
                RETURN;
            END IF;
            RETURN QUERY SELECT * FROM rate_control.reserve_permit_under_active_lease(
              requested_permit_id,requested_request_key,requested_subject_service,
              requested_subject_instance,requested_environment,requested_endpoint_authority,
              requested_endpoint_id,requested_catalog_hash,requested_canonical_hash,
              requested_parameter_hash,requested_wire_hash,requested_operation_facts_hash,
              requested_capability_hash,requested_document_hash,requested_capability_nonce,
              requested_fencing_epoch,requested_expires_at
            );
        END $$
        """
    )

    op.execute(
        f"ALTER FUNCTION rate_control.consume_permit({CONSUME_ARGUMENTS}) "
        "RENAME TO consume_permit_under_active_lease"
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.consume_permit(
            requested_permit_id varchar,
            requested_canonical_hash char(64),
            requested_parameter_hash char(64),
            requested_wire_hash char(64),
            requested_operation_facts_hash char(64),
            requested_capability_hash char(64),
            requested_document_hash char(64),
            requested_fencing_epoch bigint,
            consuming_gateway_instance_id varchar
        ) RETURNS TABLE(decision varchar, reason_code varchar, send_deadline timestamptz)
        LANGUAGE plpgsql AS $$
        DECLARE
            state_row rate_control.fencing_state%ROWTYPE;
        BEGIN
            SELECT * INTO state_row FROM rate_control.fencing_state
              WHERE singleton = true FOR UPDATE;
            IF NOT FOUND OR state_row.epoch <> requested_fencing_epoch
               OR state_row.lease_expires_at IS NULL
               OR state_row.lease_expires_at <= clock_timestamp() THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar,
                  'FENCING_EPOCH_MISMATCH'::varchar, NULL::timestamptz;
                RETURN;
            END IF;
            RETURN QUERY SELECT * FROM rate_control.consume_permit_under_active_lease(
              requested_permit_id,requested_canonical_hash,requested_parameter_hash,
              requested_wire_hash,requested_operation_facts_hash,requested_capability_hash,
              requested_document_hash,requested_fencing_epoch,consuming_gateway_instance_id
            );
        END $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION rate_control.reserve_permit({RESERVE_ARGUMENTS})")
    op.execute(
        f"ALTER FUNCTION rate_control.reserve_permit_under_active_lease({RESERVE_ARGUMENTS}) "
        "RENAME TO reserve_permit"
    )
    op.execute(f"DROP FUNCTION rate_control.consume_permit({CONSUME_ARGUMENTS})")
    op.execute(
        f"ALTER FUNCTION rate_control.consume_permit_under_active_lease({CONSUME_ARGUMENTS}) "
        "RENAME TO consume_permit"
    )
    op.execute("DROP FUNCTION rate_control.acquire_fencing_lease(varchar,bigint,integer)")
    op.execute("DROP TABLE rate_control.fencing_lease_events")
    op.execute(
        "ALTER TABLE rate_control.fencing_state DROP CONSTRAINT fencing_lease_shape, "
        "DROP COLUMN lease_expires_at, DROP COLUMN lease_renewed_at, "
        "DROP COLUMN lease_acquired_at"
    )
