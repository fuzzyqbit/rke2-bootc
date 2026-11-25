SHELL := /bin/bash
include versions.env
CONTAINER_TOOL ?= podman
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv test lint rocky-base rocky-base-push node push print-ref vmdk template survey-diff

ENV ?= dev
NEEDS_BUILD := $(filter node push print-ref vmdk,$(MAKECMDGOALS))
ifneq ($(NEEDS_BUILD),)
ifeq ($(ENV),dev)
BASE_IMAGE := $(ROCKY_BASE_IMAGE)
else ifeq ($(ENV),prod)
BASE_IMAGE := $(RHEL_BASE_IMAGE)
else
$(error ENV must be dev or prod, got '$(ENV)')
endif
ifndef BUILD
$(error BUILD is required, e.g. make $(firstword $(NEEDS_BUILD)) ENV=$(ENV) BUILD=1)
endif
RKE2_TAG := $(shell node/scripts/rke2-version.sh tag $(RKE2_VERSION))
NODE_REF = $(REGISTRY)/rke2-node-$(ENV):$(RKE2_TAG).$(BUILD)
endif

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

node:
	$(CONTAINER_TOOL) build -f node/Containerfile -t $(NODE_REF) \
	  --build-arg BASE_IMAGE=$(BASE_IMAGE) --build-arg RKE2_VERSION=$(RKE2_VERSION) \
	  --build-arg SITE_ENV=$(ENV) --build-arg RANCHER_RPM_BASEURL=$(RANCHER_RPM_BASEURL) \
	  --build-arg RKE2_CHANNEL=$(RKE2_CHANNEL) --build-arg EL_MAJOR=$(EL_MAJOR) .

push:
	$(CONTAINER_TOOL) push $(NODE_REF)

print-ref:
	@echo $(NODE_REF)

vmdk:
	disk/build-vmdk.sh $(NODE_REF) disk/config.toml output/$(ENV)

GUEST_ID ?= rhel9_64Guest
template:
	@test -n "$(VMDK)" || { echo "error: VMDK is required" >&2; exit 2; }
	@test -n "$(NAME)" || { echo "error: NAME is required" >&2; exit 2; }
	@test -n "$(DATASTORE)" || { echo "error: DATASTORE is required" >&2; exit 2; }
	@test -n "$(NETWORK)" || { echo "error: NETWORK is required" >&2; exit 2; }
	disk/make-template.sh --vmdk "$(VMDK)" --name "$(NAME)" --datastore "$(DATASTORE)" \
	  --network "$(NETWORK)" --guest-id "$(GUEST_ID)"

survey-diff:
	@test -n "$(SURVEY)" || { echo "error: SURVEY is required, e.g. make survey-diff SURVEY=survey.txt" >&2; exit 2; }
	set -euo pipefail; \
	base=$$($(CONTAINER_TOOL) run --rm $(ROCKY_BASE_IMAGE) rpm -qa --qf '%{NAME}\n') \
	  || { echo "error: could not list base packages with $(CONTAINER_TOOL) run $(ROCKY_BASE_IMAGE)" >&2; exit 1; }; \
	test -n "$$base" || { echo "error: base package list is empty" >&2; exit 1; }; \
	hack/survey-diff.sh "$(SURVEY)" <(printf '%s\n' "$$base")
