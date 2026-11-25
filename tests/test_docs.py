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
