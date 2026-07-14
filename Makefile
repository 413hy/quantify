SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv

.PHONY: help bootstrap preflight lint typecheck contracts validate-contracts validate-config \
	validate-docs validate-endpoint-catalog validate-binance-connection-contract \
	validate-capability-trust-bundle validate-mandatory-endpoint-inventory \
	test-rate-budget-contract test-binance-gateway-contract test-host-rate-startup-evidence \
	test-migrations test-locked-runtime test-unit test-property test-contract test-security security-scan \
	compose-check validate-platform-amendment validate-debian-platform build ci

help:
	@sed -n 's/^\([a-zA-Z0-9_-]*\):.*$$/\1/p' Makefile | sort

bootstrap:
	$(UV) sync --frozen --all-groups

preflight:
	@rm -f /tmp/aiq-document-package-audit.json; set +e; $(UV) run python tools/preflight_audit.py \
		--document-root /root/quantify/reference-materials/vps-archive/vps \
		--louie-root /root/quantify/reference-materials/louie-archive/louie_price_action_system_v1 \
		--pyta-root /root/quantify/reference-materials/pyta-archive/PYTA_OrderFlow_Quant_System_Spec \
		--output /tmp/aiq-document-package-audit.json; rc=$$?; \
	$(UV) run python tools/check_preflight_result.py \
		/tmp/aiq-document-package-audit.json $$rc && \
	cp /tmp/aiq-document-package-audit.json \
		evidence/preflight/2026-07-14/development/document-package-audit.json

lint:
	$(UV) run ruff check src tests tools scripts migrations

typecheck:
	$(UV) run mypy src

contracts validate-contracts:
	$(UV) run python scripts/validate/contracts.py

validate-config:
	$(UV) run python scripts/validate/config.py

validate-docs:
	$(UV) run python scripts/validate/provenance.py

validate-endpoint-catalog:
	$(UV) run python scripts/validate/config.py --only binance-endpoint-cost-catalog

validate-binance-connection-contract:
	$(UV) run python scripts/validate/config.py --only binance-connection-contract

validate-capability-trust-bundle:
	$(UV) run python scripts/validate/config.py --only capability-trust-bundle

validate-mandatory-endpoint-inventory:
	$(UV) run python scripts/validate/config.py --only binance-mandatory-endpoint-inventory

test-rate-budget-contract:
	$(UV) run pytest -q tests/unit/test_rate_budget.py tests/contract/test_immutable_contracts.py

test-binance-gateway-contract:
	$(UV) run pytest -q tests/unit/test_gateway.py

test-host-rate-startup-evidence:
	$(UV) run python scripts/validate/contracts.py --only host-rate-startup-evidence

test-migrations: build
	$(UV) run pytest -q tests/integration/test_migration_shape.py
	./scripts/test-migrations.sh

test-locked-runtime: build
	./scripts/test-locked-runtime.sh

test-unit:
	$(UV) run pytest -q tests/unit

test-contract:
	$(UV) run pytest -q tests/contract

test-security:
	$(UV) run pytest -q tests/security

test-property:
	$(UV) run pytest -q tests/property

security-scan:
	$(UV) run bandit -q -r src
	$(UV) run python scripts/validate/secret_scan.py

compose-check:
	$(UV) run python scripts/validate/compose.py
	TEST_BUSINESS_DB_PASSWORD_FILE=/tmp/not-used-business \
	TEST_HOST_DB_PASSWORD_FILE=/tmp/not-used-host \
		docker compose -f deploy/compose.test.yaml config --quiet
	AIQ_APP_IMAGE=registry.invalid/aiq-app@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
		docker compose -f deploy/host-control.compose.yaml config --quiet
	AIQ_APP_IMAGE=registry.invalid/aiq-app@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
		docker compose -f deploy/binance-egress.compose.yaml config --quiet
	AIQ_APP_IMAGE=registry.invalid/aiq-app@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
		docker compose -f deploy/compose.yaml config --quiet

validate-debian-platform:
	./scripts/validate/debian-platform.sh

validate-platform-amendment:
	$(UV) run python scripts/validate/platform_amendment.py

build:
	docker build --pull=false --provenance=false -f docker/app.Dockerfile -t aiq-app:m0 .

ci: lint typecheck validate-contracts validate-config validate-docs validate-platform-amendment compose-check test-unit test-property test-contract test-security security-scan
