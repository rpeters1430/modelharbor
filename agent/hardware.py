"""This PC's GPU and memory, so the dashboard can suggest files that suit it. Windows only; None elsewhere."""
import ctypes
import sys

# Display adapters; the driver records the dedicated memory here, and ComfyUI need not be running.
GPU_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


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


def detect():
    if sys.platform != "win32":
        return None
    gpu, ram = _gpu(), _ram()
    if not gpu and not ram:
        return None
    return {"gpu": gpu and gpu[0], "vendor": gpu and vendor(gpu[0]), "vram_bytes": gpu and gpu[1], "ram_bytes": ram}
