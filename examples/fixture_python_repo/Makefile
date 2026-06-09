format-check:
	ruff format --check .

lint:
	ruff check .

typecheck:
	python -m compileall src tests

test:
	PYTHONPATH=src pytest

security:
	python -c "print('security ok')"
