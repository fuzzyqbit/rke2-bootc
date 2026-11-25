import yaml
from conftest import REPO
R = REPO / "node/rootfs"

def test_cloud_init_config():
    cfg = yaml.safe_load((R / "etc/cloud/cloud.cfg.d/90-rke2-node.cfg").read_text())
    assert cfg["datasource_list"] == ["OVF", "VMware", "None"]
    assert cfg["disable_vmware_customization"] is False
    assert cfg["users"] == [] and cfg["ssh_pwauth"] is False
    assert cfg["growpart"] == {"mode": "off"} and cfg["resize_rootfs"] is False

def test_networkmanager_ignores_cni_interfaces():
    text = (R / "usr/lib/NetworkManager/conf.d/90-rke2-cni.conf").read_text()
    # A device section: the role writes [keyfile] unmanaged-devices under /etc, which would override ours.
    assert "[device-rke2-cni]" in text and "[keyfile]" not in text and "unmanaged-devices" not in text
    match = next(l for l in text.splitlines() if l.startswith("match-device="))
    for name in ("cali*", "flannel*", "vxlan.calico", "vxlan-v6.calico", "tunl*", "cilium*", "lxc*"):
        assert f"interface-name:{name}" in match
    assert "managed=0" in text.splitlines()

def test_etcd_user_and_sudo_and_tmpfiles():
    assert (R / "usr/lib/sysusers.d/rke2-etcd.conf").read_text().startswith("u etcd ")
    assert (R / "etc/sudoers.d/10-wheel-nopasswd").read_text().strip() == "%wheel ALL=(ALL) NOPASSWD: ALL"
    assert "d /var/lib/rancher/rke2 0755 root root -" in (R / "usr/lib/tmpfiles.d/rke2-node.conf").read_text()

def test_growroot_unit():
    unit = (R / "usr/lib/systemd/system/rke2-node-growroot.service").read_text()
    for line in ("Type=oneshot", "MountFlags=slave", "ExecStart=/usr/libexec/rke2-node/growroot",
                 "Before=rke2-server.service rke2-agent.service"):
        assert line in unit
