SHELL := /bin/bash
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv test lint

venv: $(BIN)/pytest

$(BIN)/pytest: requirements-dev.txt
	python3 -m venv $(VENV)
	$(BIN)/pip install -q -r requirements-dev.txt
	@touch $@

test: venv
	$(BIN)/pytest -q

lint: venv
	export PATH="$(CURDIR)/$(BIN):$$PATH"; \
	scripts=$$( { git ls-files -co --exclude-standard '*.sh'; \
	  if [ -d node/rootfs/usr ]; then find node/rootfs/usr -type f ! -name '*.*'; fi; } | sort -u ); \
	if [ -n "$$scripts" ]; then shellcheck $$scripts; fi; \
	yamllint -s . && \
	if [ -d ansible ]; then \
	  ansible-lint ansible/ && \
	  for pb in ansible/*.yml; do ansible-playbook --syntax-check "$$pb"; done; \
	fi
