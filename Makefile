PROFILE ?= default
GOLDEN_MONTHS ?= 2026-01 2026-02 2026-03 2026-04
UV_CACHE_DIR ?= /private/tmp/uv-cache

.PHONY: regression-golden regression-seed-golden

regression-golden:
	@set -e; for month in $(GOLDEN_MONTHS); do \
		echo "==> Regression $$month"; \
		UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python main.py regression --profile $(PROFILE) --export-date $$month; \
	done

regression-seed-golden:
	@set -e; for month in $(GOLDEN_MONTHS); do \
		echo "==> Seeding regression $$month"; \
		UV_CACHE_DIR=$(UV_CACHE_DIR) uv run python main.py regression --profile $(PROFILE) --export-date $$month --seed-missing-approvals; \
	done

release-%:
	hatch version $*
	git add pyproject.toml
	git commit -m "chore: release $$(hatch version)"
	git push
