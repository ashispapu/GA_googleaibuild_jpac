export PYTHONPATH := .
mock:      ## mock API on :8000 (docs at /docs)
	python -m uvicorn mock_server.app:app --reload --port 8000
test:      ## contract tests against the mock
	pytest -q
test-p1:   ## same API tests against P1's real backend
	PYTHONPATH=.:p1-backend PLANTGUARD_APP=app.main:app pytest -q tests/test_contracts.py -k "not fixture and not agent_events and not story_numbers and not tools_have"
test-p1-unit: ## P1's own unit tests (security, gateway, incidents, OT, scoring, Keycloak mode)
	PYTHONPATH=.:p1-backend pytest -q p1-backend/tests
run-p1:    ## real backend on :8000 (docs at /docs)
	PYTHONPATH=.:p1-backend python -m uvicorn app.main:app --reload --port 8000
up:        ## whole stack in Docker (api, timescale, redis, qdrant, mosquitto)
	docker compose up --build -d
up-sso:    ## same, plus Keycloak (set AUTH_MODE=keycloak in .env)
	docker compose --profile sso up --build -d
down:      ## stop the stack (add -v to wipe data)
	docker compose --profile sso down
export:    ## regenerate fixtures, openapi, tools, TS types (run after any schema change)
	python fixtures/build_fixtures.py && python scripts_export.py && npx -y openapi-typescript@7 generated/openapi.json -o generated/api.d.ts
