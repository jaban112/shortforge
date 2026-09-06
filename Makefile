.PHONY: install test doctor sample run auth stats

install:
	pip install -e ".[dev]"

test:
	python -m pytest -q

doctor:
	python -m shortforge doctor

# offline: renders fixtures/otd_1620.json with the real TTS + ffmpeg, no network needed except the one-time model download
sample:
	python -m shortforge render-fixture fixtures/otd_1620.json

run:
	python -m shortforge run

auth:
	python -m shortforge auth

stats:
	python -m shortforge stats
