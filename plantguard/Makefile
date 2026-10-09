export PYTHONPATH := .
mock:      ## mock API on :8000 (docs at /docs)
	python -m uvicorn mock_server.app:app --reload --port 8000
test:      ## contract tests against the mock
	pytest -q
test-p1:   ## same API tests against P1's real backend
	PYTHONPATH=.:p1-backend PLANTGUARD_APP=app.main:app pytest -q tests/test_contracts.py -k "not fixture and not agent_events and not story_numbers and not tools_have"
export:    ## regenerate fixtures, openapi, tools, TS types (run after any schema change)
	python fixtures/build_fixtures.py && python scripts_export.py && npx -y openapi-typescript@7 generated/openapi.json -o generated/api.d.ts
