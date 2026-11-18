.PHONY: check check-openapi-contract e2e-local

# Optional ignored local override files.
-include .env.e2e.local
export E2E_SPACE_KEY

E2E_SPACE_ID ?= 9c23b032-f032-498c-b009-1a3d711d642b
E2E_AGENT_PRODUCT ?= AntCC

check: check-openapi-contract

check-openapi-contract:
	./aivision-py-be/scripts/run_openapi_contract_tests.sh

e2e-local:
	@test -n "$$E2E_SPACE_KEY" || (echo "E2E_SPACE_KEY is required. Set it in the environment or .env.e2e.local"; exit 1)
	cd aivision-py-be && \
		E2E_SMOKE=1 \
		E2E_SPACE_ID="$(E2E_SPACE_ID)" \
		E2E_SPACE_KEY="$$E2E_SPACE_KEY" \
		E2E_AGENT_PRODUCT="$(E2E_AGENT_PRODUCT)" \
		uv run python manage.py test apps.core.tests.test_openapi_e2e_smoke -v2
