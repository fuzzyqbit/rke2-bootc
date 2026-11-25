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

def test_syntax_and_lint():
    r = run([str(REPO / ".venv/bin/ansible-playbook"), "--syntax-check", "-i", "ansible/inventory.example/hosts.yml",
             "ansible/rolling-update.yml", "-e", "target_image=r.example.com/x:v1"])
    assert r.returncode == 0, r.stderr
    assert run([str(REPO / ".venv/bin/ansible-lint"), "ansible/"]).returncode == 0
