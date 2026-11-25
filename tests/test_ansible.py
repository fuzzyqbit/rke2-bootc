import os

import yaml
from conftest import REPO, run
A = REPO / "ansible"
load = lambda p: yaml.safe_load((A / p).read_text())

def test_inventory_version_matches_versions_env():
    env = dict(l.split("=", 1) for l in (REPO / "versions.env").read_text().split())
    assert load("inventory.example/group_vars/all.yml")["rke2_install_version"] == env["RKE2_VERSION"]

def test_inventory_groups_use_role_names():
    text = (A / "inventory.example/hosts.yml").read_text()
    assert "rke2_servers" in text and "rke2_agents" in text

def test_play_order_and_safety_settings():
    plays = load("rolling-update.yml")
    assert [p["hosts"] for p in plays] == ["rke2_servers:rke2_agents", "rke2_servers", "rke2_agents"]
    assert plays[1]["serial"] == 1 and plays[2]["serial"] == "{{ agent_batch }}"
    for p in plays:
        assert p["any_errors_fatal"] is True and p["max_fail_percentage"] == 0

def test_preflight_refuses_non_bootc_nodes_and_moving_tags():
    text = (A / "rolling-update.yml").read_text()
    assert "not a bootc node" in text and "/run/ostree-booted" in text and "latest" in text

def test_update_node_task_order():
    names = [t["name"] for t in load("tasks/update-node.yml")]
    assert names == ["Read bootc status", "Choose kubectl host", "Read update marker",
                     "Write registry credentials", "Stage target image", "Mark and cordon node", "Drain node",
                     "Reboot into new image", "Wait for node Ready", "Wait for API server and etcd",
                     "Verify booted image", "Uncordon and clear marker"]

def test_credentials_are_not_logged():
    task = next(t for t in load("tasks/update-node.yml") if t["name"] == "Write registry credentials")
    assert task["no_log"] is True and "registry_auth_json is defined" in str(task["when"])

def test_interrupted_node_is_finished_but_operator_cordon_is_respected():
    tasks = {t["name"]: t for t in load("tasks/update-node.yml")}
    for name in ("Wait for node Ready", "Verify booted image", "Uncordon and clear marker"):
        assert "marker" in str(tasks[name]["when"])          # runs for a node left mid-update
    for name in ("Stage target image", "Drain node", "Reboot into new image"):
        assert "marker" not in str(tasks[name]["when"])      # never re-done for a node already on target
    assert "update_marker" in str(tasks["Mark and cordon node"])

def test_operator_cordon_survives_update():
    tasks = {t["name"]: t for t in load("tasks/update-node.yml")}
    for name in ("Mark and cordon node", "Uncordon and clear marker"):
        assert "was_cordoned_annotation" in str(tasks[name])
    assert "unschedulable" in str(tasks["Mark and cordon node"])

def test_tunables_are_overridable_from_inventory():
    plays = load("rolling-update.yml")
    for p in plays[1:]:
        for name in ("drain_timeout", "ready_timeout", "k8s_node_name", "kubectl"):
            assert name not in p["vars"]       # play vars would outrank inventory vars
    assert plays[2]["vars"]["agent_batch"] == 1

def test_ready_wait_is_bounded_and_checks_boot_id():
    text = (A / "tasks/update-node.yml").read_text()
    assert "kubectl }} wait" not in text and "wait --for" not in text   # no nested kubectl retries
    task = next(t for t in load("tasks/update-node.yml") if t["name"] == "Wait for node Ready")
    assert "bootID" in str(task["command"] if "command" in task else task) or "bootID" in str(task)
    assert "Ready" in str(task) and "node_boot_before" in str(task["until"])
    assert "_ready_timeout" in str(task["retries"]) and task["delay"] == 10

def test_syntax_and_lint():
    env = {**os.environ, "ANSIBLE_DEPRECATION_WARNINGS": "False"}
    r = run([str(REPO / ".venv/bin/ansible-playbook"), "--syntax-check", "-i", "ansible/inventory.example/hosts.yml",
             "ansible/rolling-update.yml", "-e", "target_image=r.example.com/x:v1"], env=env)
    assert r.returncode == 0, r.stderr
    assert run([str(REPO / ".venv/bin/ansible-lint"), "ansible/"], env=env).returncode == 0

ROLE_REPO_KEYS = {"name", "description", "baseurl", "gpgcheck", "gpgkey", "enabled"}

def test_repo_dicts_have_every_key_the_role_reads():
    # The inventory dicts replace the role defaults as a whole; roles/rke2/tasks/rpm_install.yml
    # dereferences exactly these keys, so a missing one fails before the dnf task.
    gv = load("inventory.example/group_vars/all.yml")
    for name in ("rke2_common_yum_repo", "rke2_versioned_yum_repo"):
        assert set(gv[name]) == ROLE_REPO_KEYS, name
        assert gv[name]["enabled"] is True and gv[name]["gpgcheck"] is True

def test_commented_mirror_alternative_is_complete_too():
    text = (A / "inventory.example/group_vars/all.yml").read_text()
    block = text.split("# Internal mirror alternative:\n", 1)[1]
    alt = yaml.safe_load("\n".join(l[2:] for l in block.splitlines() if l.startswith("# ")))
    for name in ("rke2_common_yum_repo", "rke2_versioned_yum_repo"):
        assert set(alt[name]) == ROLE_REPO_KEYS, name

def test_example_image_names_are_real():
    text = (A / "rolling-update.yml").read_text()
    assert "rke2-node:" not in text and "rke2-node-dev:" in text

def _render(expr, **variables):
    import jinja2
    return jinja2.Environment().from_string(expr).render(**variables)

def test_rke2_version_is_checked_after_the_reboot():
    verify = next(t for t in load("tasks/update-node.yml") if t["name"] == "Verify booted image")
    steps = verify["block"]
    assert "bootc status" in steps[0]["ansible.builtin.command"] and "target_image" in steps[0]["failed_when"]
    assert steps[1]["ansible.builtin.setup"]["filter"] == "ansible_local"
    check = steps[2]["ansible.builtin.assert"]
    assert "ansible_local.rke2_image.rke2_version" in check["that"] and "_expected_rke2_version" in check["that"]
    assert "rke2_install_version" in check["fail_msg"] and "{{ _expected_rke2_version }}" in check["fail_msg"]
    assert "ansible_local.rke2_image.rke2_version" in check["fail_msg"]
    assert steps[2]["when"] == "_expected_rke2_version | length > 0"

def test_rke2_version_check_is_skipped_when_no_version_is_set():
    plays = load("rolling-update.yml")
    expr = plays[1]["vars"]["_expected_rke2_version"]
    assert plays[2]["vars"]["_expected_rke2_version"] == expr
    for name in ("expected_rke2_version", "rke2_install_version"):
        assert name not in plays[1]["vars"]          # inventory values must not be outranked
    assert _render(expr) == ""                                                   # check skipped
    assert _render(expr, rke2_install_version="v1.34.3+rke2r1") == "v1.34.3+rke2r1"
    assert _render(expr, rke2_install_version="v1.34.3+rke2r1",
                   expected_rke2_version="v1.35.0+rke2r1") == "v1.35.0+rke2r1"

def test_ready_poll_has_a_request_timeout():
    task = next(t for t in load("tasks/update-node.yml") if t["name"] == "Wait for node Ready")
    assert "--request-timeout=10s" in task["ansible.builtin.command"]
