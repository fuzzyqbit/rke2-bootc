SHELL := /bin/bash
include versions.env
CONTAINER_TOOL ?= podman
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv test lint rocky-base rocky-base-push

venv: $(BIN)/pytest

$(BIN)/pytest: requirements-dev.txt
	python3 -m venv $(VENV)
	$(BIN)/pip install -q -r requirements-dev.txt
	@touch $@

test: venv
	$(BIN)/pytest -q

lint: venv
	set -euo pipefail; \
	export PATH="$(CURDIR)/$(BIN):$$PATH"; \
	scripts=$$( { git ls-files -co --exclude-standard '*.sh'; \
	  if [ -d node/rootfs/usr ]; then find node/rootfs/usr -type f ! -name '*.*'; fi; } | sort -u ); \
	if [ -n "$$scripts" ]; then shellcheck $$scripts; fi; \
	yamllint -s .; \
	if [ -d ansible ]; then \
	  ansible-lint ansible/; \
	  shopt -s nullglob; \
	  playbooks=(ansible/*.yml); \
	  if [ "$${#playbooks[@]}" -eq 0 ]; then echo "no playbooks in ansible/" >&2; exit 1; fi; \
	  for pb in "$${playbooks[@]}"; do ansible-playbook --syntax-check "$$pb"; done; \
	fi

rocky-base:
	$(CONTAINER_TOOL) build -f base/rocky/Containerfile -t $(ROCKY_BASE_IMAGE) \
	  --build-arg ROCKY_MIRROR=$(ROCKY_MIRROR) --build-arg EL_MAJOR=$(EL_MAJOR) .

rocky-base-push:
	$(CONTAINER_TOOL) push $(ROCKY_BASE_IMAGE)
