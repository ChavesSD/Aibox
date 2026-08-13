"""Enumeração USB no Windows (CfgMgr32) para diagnosticar totem/ADB."""
from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass

_VIDPID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", re.I)
_MI_RE = re.compile(r"MI_([0-9A-Fa-f]{2})", re.I)

# cfgmgr32.h
CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
CM_GETIDLIST_FILTER_PRESENT = 0x00000100
CR_SUCCESS = 0


@dataclass(frozen=True)
class PresentUsb:
    vid: str  # hex maiúsculo, 4 dígitos
    pid: str
    instance: str
    mi: str | None = None  # interface composta, ex. "01"


def _device_id_list(enumerator: str) -> list[str]:
    if os.name != "nt":
        return []
    cfg = ctypes.WinDLL("cfgmgr32")
    cfg.CM_Get_Device_ID_List_SizeW.argtypes = [
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_wchar_p,
        ctypes.c_ulong,
    ]
    cfg.CM_Get_Device_ID_List_SizeW.restype = ctypes.c_ulong
    cfg.CM_Get_Device_ID_ListW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    cfg.CM_Get_Device_ID_ListW.restype = ctypes.c_ulong
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    size = ctypes.c_ulong(0)
    cr = cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), enumerator, flags)
    if cr != CR_SUCCESS or size.value <= 1:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    cr = cfg.CM_Get_Device_ID_ListW(enumerator, buf, size, flags)
    if cr != CR_SUCCESS:
        return []
    raw = buf[: size.value]
    parts = raw.split("\x00")
    return [p for p in parts if p]


def list_present_usb() -> list[PresentUsb]:
    """Dispositivos USB atualmente conectados."""
    out: list[PresentUsb] = []
    seen: set[str] = set()
    try:
        ids = _device_id_list("USB")
    except Exception:
        return []
    for dev_id in ids:
        key = dev_id.upper()
        if key in seen:
            continue
        seen.add(key)
        m = _VIDPID_RE.search(dev_id)
        if not m:
            continue
        vid, pid = m.group(1).upper(), m.group(2).upper()
        mi_m = _MI_RE.search(dev_id)
        mi = mi_m.group(1).upper() if mi_m else None
        instance = dev_id.split("\\")[-1] if "\\" in dev_id else dev_id
        out.append(PresentUsb(vid=vid, pid=pid, instance=instance, mi=mi))
    return out


def allwinner_mode(devices: list[PresentUsb] | None = None) -> str | None:
    """
    Totens Allwinner (Intelite/PROSB):
    - PID 1006 = só armazenamento (sem ADB)
    - PID 1007 + MI_01 = interface ADB
    """
    devices = devices if devices is not None else list_present_usb()
    aw = [d for d in devices if d.vid == "1F3A"]
    if not aw:
        return None
    if any(d.pid == "1007" and d.mi == "01" for d in aw):
        return "adb"
    if any(d.pid == "1006" for d in aw):
        return "storage"
    if any(d.pid == "1007" for d in aw):
        return "composite"
    return "other"


def samsung_mtp_present(devices: list[PresentUsb] | None = None) -> bool:
    devices = devices if devices is not None else list_present_usb()
    return any(d.vid == "04E8" and d.pid in {"6860", "685D", "6863"} for d in devices)
