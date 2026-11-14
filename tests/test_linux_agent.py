"""The agent on a Linux PC: config location, ComfyUI discovery, GPU detection and download folders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import hardware
import modelharbor_agent as agent
from download_worker import existing_folder

PCI_IDS = """# comment
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t7550  Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]
\t\t1002 0b00  Radeon RX 9070 XT
8086  Intel Corporation
\ta780  Raptor Lake-S GT1 [UHD Graphics 770]
"""


def _card(drm, name, vendor, device, vram=None):
    d = drm / name / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "device").write_text(device + "\n")
    if vram is not None:
        (d / "mem_info_vram_total").write_text(str(vram) + "\n")


def test_linux_gpu_picks_the_card_with_dedicated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: None)
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    drm = tmp_path / "drm"
    _card(drm, "card1", "0x8086", "0xa780")  # integrated: no VRAM figure
    _card(drm, "card2", "0x1002", "0x7550", 17095983104)
    (drm / "card2-DP-3").mkdir()  # a connector, not a GPU
    name, vram = hardware._linux_gpu(drm, (ids,))
    assert name == "AMD Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]" and vram == 17095983104
    assert hardware.vendor(name) == "amd"


def test_linux_gpu_prefers_a_bigger_nvidia_card(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: ("NVIDIA GeForce RTX 4090", 24 * 1024**3))
    assert hardware._linux_gpu(tmp_path / "none", ()) == ("NVIDIA GeForce RTX 4090", 24 * 1024**3)


def test_nvidia_smi_output_is_parsed(monkeypatch):
    import subprocess
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    out = "NVIDIA GeForce RTX 3060, 12288\nNVIDIA GeForce RTX 4090, 24564\n"
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, "")
    assert hardware._nvidia_smi(run) == ("NVIDIA GeForce RTX 4090", 24564 * 1024**2)


def test_pci_name_unknown_device(tmp_path):
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    assert hardware.pci_name("0x1002", "0x1234", (ids,)) is None
    assert hardware.pci_name("0x1002", "0x7550", (tmp_path / "missing",)) is None


def test_linux_ram(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       131597904 kB\nMemFree: 1 kB\n")
    assert hardware._linux_ram(meminfo) == 131597904 * 1024


def test_discover_roots_finds_a_clone_one_folder_down(tmp_path):
    (tmp_path / "AI" / "ComfyUI" / "models").mkdir(parents=True)
    (tmp_path / "ComfyUI" / "models").mkdir(parents=True)
    assert agent.discover_roots(tmp_path) == [str(tmp_path / "ComfyUI" / "models"),
                                              str(tmp_path / "AI" / "ComfyUI" / "models")]


def test_config_lives_in_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.os, "name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert agent.config_dir() == tmp_path / "ModelHarbor"


def test_download_reuses_a_folder_with_other_case(tmp_path):
    (tmp_path / "loras" / "sdxl").mkdir(parents=True)
    assert existing_folder(tmp_path, "loras", "SDXL") == tmp_path / "loras" / "sdxl"
    assert existing_folder(tmp_path, "loras", "Flux") == tmp_path / "loras" / "Flux"  # created later by mkdir
    assert existing_folder(tmp_path, "checkpoints", "") == tmp_path / "checkpoints"
