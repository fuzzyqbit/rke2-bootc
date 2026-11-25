# tests/test_docs.py
import re
from conftest import REPO, run
DOCS = ["README.md", "docs/verification-checklist.md", "docs/migration.md", "docs/operations.md", "site/README.md"]

def test_every_make_target_mentioned_exists():
    targets = set(re.findall(r"^([a-z][a-z-]*):", (REPO / "Makefile").read_text(), re.M))
    for doc in DOCS:
        for t in re.findall(r"\bmake ([a-z][a-z-]*)", (REPO / doc).read_text()):
            assert t in targets, f"{doc}: make {t}"

def test_every_repo_path_mentioned_exists():
    for doc in DOCS:
        for p in re.findall(r"`((?:node|disk|hack|ansible|site|base|docs)/[\w./-]+)`", (REPO / doc).read_text()):
            assert (REPO / p).exists() or p == "disk/config.toml", f"{doc}: {p}"

def test_checklist_opens_with_the_three_experiments():
    text = (REPO / "docs/verification-checklist.md").read_text()
    order = [text.index(h) for h in ("## Three experiments first", "### 1. Rocky base builds and boots",
             "### 2. The role's install step is a no-op", "### 3. Customization spec on a clone", "## Full checklist")]
    assert order == sorted(order)
    assert "stop and report" in text and "guestinfo" in text and "masked" in text

def test_readme_states_what_is_unproven():
    assert "verification-checklist.md" in (REPO / "README.md").read_text()

def test_whole_suite_and_lint_pass():
    assert run(["make", "lint"]).returncode == 0

def _doc(path):
    return (REPO / path).read_text()

def test_checklist_follows_the_fix_wave():
    text = _doc("docs/verification-checklist.md")
    assert "mkdir -p ~/inventories" in text and "rke2_agents:\n      hosts: {}" in text   # experiment 2
    assert "to: default" not in text and "to: 0.0.0.0/0" in text
    assert "journalctl -b --grep 'avc: +denied'" in text and "vmware_cust_file_max_wait" in text
    assert "chronyc sources" in text and "sysusers.d" in text
    assert "--device /dev/fuse" in text and "try the\n  build once by hand" not in text

def test_runbooks_state_the_rules_the_role_needs():
    migration = _doc("docs/migration.md")
    assert "rke2_kubernetes_api_server_host" in migration and "never first" in migration
    assert "BASE_IMAGE=" in migration
    ops = _doc("docs/operations.md")
    assert "## OS updates" in ops and "podman image inspect" in ops and "expected_rke2_version" in ops
    assert "unless it still carries the update marker" in ops and "agent_batch` workers" in ops
    site = _doc("site/README.md")
    assert "sysusers.d" in site and "ROCKY_MIRROR" in site and "`*.key`" in site
    assert "noreplace" not in site and "rpmnew" not in site
    assert "The site layer has the last word" in site and "copied again" in site

def test_readme_covers_template_and_build_host_details():
    text = _doc("README.md")
    for needle in ("stream-optimized", "GOVC_INSECURE", "TEMPLATE_ARGS", "unique hostname", "-link=false",
                   "0.56", "fingerprint"):
        assert needle in text, needle
