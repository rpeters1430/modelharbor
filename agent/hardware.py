"""This PC's GPU and memory, so the dashboard can suggest files that suit it. Windows and Linux; None elsewhere."""
import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

# Display adapters; the driver records the dedicated memory here, and ComfyUI need not be running.
GPU_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
DRM = Path("/sys/class/drm")
PCI_IDS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"), Path("/usr/share/pci.ids"))
PCI_VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}


def vendor(name):
    n = name.lower()
    if any(w in n for w in ("nvidia", "geforce", "rtx", "quadro")):
        return "nvidia"
    if any(w in n for w in ("amd", "radeon")):
        return "amd"
    return "intel" if any(w in n for w in ("intel", "arc")) else "other"


def _gpu():
    import winreg
    best = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GPU_CLASS) as adapters:
            for i in range(winreg.QueryInfoKey(adapters)[0]):
                sub = winreg.EnumKey(adapters, i)
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(adapters, sub) as key:
                        name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                        vram = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                except OSError:
                    continue  # integrated GPUs have no dedicated memory value
                vram = int.from_bytes(vram, "little") if isinstance(vram, bytes) else int(vram)
                if not best or vram > best[1]:
                    best = (name[:120], vram)
    except OSError:
        pass
    return best


def _ram():
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ("total", "avail", "page", "avail_page", "virtual", "avail_virtual", "ext")]
    status = Status()
    status.length = ctypes.sizeof(Status)
    return status.total if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def pci_name(vendor_id, device_id, ids=PCI_IDS):
    """"Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]" for 0x1002/0x7550, from the system's pci.ids; None if unknown."""
    want_vendor, want_device = vendor_id.removeprefix("0x").lower(), device_id.removeprefix("0x").lower()
    for path in ids:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        in_vendor = False
        for line in lines:
            if not line or line.startswith("#"):
                continue
            if not line.startswith("\t"):
                in_vendor = line[:4].lower() == want_vendor
            elif in_vendor and not line.startswith("\t\t") and line[1:5].lower() == want_device:
                return line[5:].strip()
        return None
    return None


def _nvidia_smi(run=subprocess.run):
    """(name, vram bytes) of the largest NVIDIA GPU; the kernel driver does not publish VRAM in sysfs."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    best = None
    for line in out.splitlines():
        name, _, mib = line.rpartition(",")
        try:
            vram = int(float(mib)) * 1024**2
        except ValueError:
            continue
        if name.strip() and (not best or vram > best[1]):
            best = (name.strip()[:120], vram)
    return best


def _linux_gpu(drm=DRM, ids=PCI_IDS):
    """(name, vram bytes) of the GPU with the most dedicated memory. amdgpu and Intel's xe driver publish
    it in sysfs; integrated GPUs have none and are skipped."""
    best = None
    for card in sorted(drm.glob("card[0-9]*")):
        if "-" in card.name:  # card1-HDMI-A-1 is a connector, not a GPU
            continue
        device = card / "device"
        total = _read(device / "mem_info_vram_total")
        if not total or not total.isdigit() or not int(total):
            continue
        vendor_id, device_id = _read(device / "vendor") or "", _read(device / "device") or ""
        name = _read(device / "product_name") or pci_name(vendor_id, device_id, ids) or "GPU " + device_id
        maker = PCI_VENDORS.get(vendor_id.lower())
        if maker and not name.lower().startswith(maker.lower()):
            name = maker + " " + name
        if not best or int(total) > best[1]:
            best = (name[:120], int(total))
    nvidia = _nvidia_smi()
    return nvidia if nvidia and (not best or nvidia[1] > best[1]) else best


def _linux_ram(meminfo=Path("/proc/meminfo")):
    for line in (_read(meminfo) or "").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return None


def detect():
    if sys.platform == "win32":
        gpu, ram = _gpu(), _ram()
    elif sys.platform.startswith("linux"):
        gpu, ram = _linux_gpu(), _linux_ram()
    else:
        return None
    if not gpu and not ram:
        return None
    return {"gpu": gpu and gpu[0], "vendor": gpu and vendor(gpu[0]), "vram_bytes": gpu and gpu[1], "ram_bytes": ram}
