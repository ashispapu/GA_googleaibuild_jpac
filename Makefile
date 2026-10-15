export PYTHONPATH := .
mock:      ## mock API on :8000 (docs at /docs)
	python -m uvicorn mock_server.app:app --reload --port 8000
test:      ## contract tests against the mock
	pytest -q
test-p1:   ## same API tests against P1's real backend
	PYTHONPATH=.:p1-backend PLANTGUARD_APP=app.main:app pytest -q tests/test_contracts.py -k "not fixture and not agent_events and not story_numbers and not tools_have"
test-p1-unit: ## P1's own unit tests (security, gateway, incidents, OT, scoring, Keycloak mode)
	PYTHONPATH=.:p1-backend pytest -q p1-backend/tests
test-p2:   ## P2 agent layer: harness, LiteLLM adapter, tools, flows, hand-off/sub-agents, backend integration
	PYTHONPATH=.:p2-agents pytest -q p2-agents/tests
run-p1:    ## real backend on :8000 with the live agents (p2-agents/config/agents.ini; needs GEMINI_API_KEY, else replay)
	PYTHONPATH=.:p1-backend:p2-agents python -m uvicorn app.main:app --reload --port 8000
test-dist: ## Postgres/Redis/Qdrant behaviour (needs PLANTGUARD_TEST_PG, _REDIS, _QDRANT_URL, _QDRANT_ADMIN_KEY)
	PYTHONPATH=.:p1-backend pytest -q -rs p1-backend/tests/integration/test_distributed_backends.py
up:        ## production architecture in Docker: postgres, redis, qdrant, migrate, engine, worker x2, api x2, web
	docker compose up --build -d
up-sso:    ## same, plus Keycloak (set AUTH_MODE=keycloak in .env)
	docker compose --profile sso up --build -d
down:      ## stop the stack (add -v to wipe data)
	docker compose --profile sso down
migrate:   ## schema + seed + vector index for the configured databases (idempotent)
	PYTHONPATH=.:p1-backend:p2-agents python -m app.ops migrate
deploy-check: ## lint the GCP deploy scripts
	bash -n deploy/gcp/*.sh deploy/qdrant/*.sh && shellcheck -x -S warning -e SC1091,SC2034 deploy/gcp/*.sh deploy/qdrant/*.sh
export:    ## regenerate fixtures, openapi, tools, TS types (run after any schema change)
	python fixtures/build_fixtures.py && python scripts_export.py && npx -y openapi-typescript@7 generated/openapi.json -o generated/api.d.ts
