"""Reinicia o aparelho e confere se o Autostart abriu o app depois do boot (sem Qt, testável)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass
class RebootResult:
    ok: bool
    message: str
    stage: str  # reboot | offline | online | boot | autostart | done


def _is_wifi_serial(serial: str) -> bool:
    return ":" in serial and "." in serial


def _device_state(adb, serial: str) -> str | None:
    try:
        for d in adb.list_devices():
            if d.serial == serial:
                return d.state
    except Exception:
        return None
    return None


def wait_and_validate(
    adb,
    serial: str,
    address: str | None,
    expected_pkgs: Sequence[str],
    status: Callable[[str], None],
    should_stop: Callable[[], bool] = lambda: False,
    *,
    offline_timeout: float = 60.0,
    online_timeout: float = 240.0,
    boot_timeout: float = 120.0,
    focus_timeout: float = 120.0,
    poll_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RebootResult:
    """Reinicia, espera o aparelho voltar e confere se algum app do Autostart ficou em primeiro plano."""
    pkgs = [p for p in dict.fromkeys(expected_pkgs) if p]
    reconnect = address or (serial if _is_wifi_serial(serial) else None)

    status("Reiniciando o equipamento para validar o Autostart…")
    try:
        adb.reboot(serial)
    except Exception as exc:  # noqa: BLE001
        return RebootResult(False, f"Não foi possível reiniciar o aparelho: {exc}", "reboot")

    # 1) cai da lista de dispositivos
    deadline = clock() + offline_timeout
    went_offline = False
    while clock() < deadline:
        if should_stop():
            return RebootResult(False, "Validação cancelada.", "offline")
        if _device_state(adb, serial) != "device":
            went_offline = True
            break
        sleep(poll_s)
    if not went_offline:
        return RebootResult(
            False,
            "O aparelho não reiniciou (continuou conectado). Reinicie-o manualmente e confira se o app abre sozinho.",
            "offline",
        )
    status("Aparelho reiniciando… aguardando voltar.")

    # 2) volta a ficar online (Wi‑Fi: reconecta periodicamente)
    deadline = clock() + online_timeout
    last_connect = 0.0
    online = False
    while clock() < deadline:
        if should_stop():
            return RebootResult(False, "Validação cancelada.", "online")
        if reconnect and clock() - last_connect >= 5.0:
            last_connect = clock()
            try:
                adb.connect(reconnect)
            except Exception:
                pass
        if _device_state(adb, serial) == "device":
            online = True
            break
        sleep(poll_s)
    if not online:
        hint = (
            " Depois do boot a depuração sem fio pode ter sido desativada: reative-a no aparelho e reconecte."
            if reconnect
            else ""
        )
        return RebootResult(
            False,
            f"O aparelho não voltou a responder em {int(online_timeout)} s.{hint}",
            "online",
        )
    status("Aparelho online. Aguardando o Android terminar de iniciar…")

    # 3) boot concluído
    deadline = clock() + boot_timeout
    booted = False
    while clock() < deadline:
        if should_stop():
            return RebootResult(False, "Validação cancelada.", "boot")
        out = adb.shell_try(serial, "getprop sys.boot_completed", timeout_s=5)
        if out is not None and out.strip().endswith("1"):
            booted = True
            break
        sleep(poll_s)
    if not booted:
        return RebootResult(False, "O Android não terminou de iniciar a tempo.", "boot")

    if not pkgs:
        return RebootResult(True, "Aparelho reiniciado e iniciado normalmente.", "done")

    # 4) Autostart abriu algum dos apps configurados
    status("Android iniciado. Conferindo se o Autostart abriu o app…")
    deadline = clock() + focus_timeout
    running_only = False
    while clock() < deadline:
        if should_stop():
            return RebootResult(False, "Validação cancelada.", "autostart")
        focus = adb.shell_try(
            serial, "dumpsys window 2>/dev/null | grep -m 1 -E 'mCurrentFocus|mFocusedApp'", timeout_s=8
        )
        if focus:
            for pkg in pkgs:
                if pkg in focus:
                    return RebootResult(True, f"Autostart validado: {pkg} abriu sozinho após o reinício.", "done")
        for pkg in pkgs:
            pid = adb.shell_try(serial, f"pidof {pkg}", timeout_s=5)
            if pid and pid.strip().split()[:1] and pid.strip().split()[0].isdigit():
                running_only = True
        sleep(poll_s * 2)

    if running_only:
        return RebootResult(
            True,
            "Aparelho reiniciou e o app está em execução, mas não aparece em primeiro plano. Confira na tela.",
            "done",
        )
    return RebootResult(
        False,
        "O aparelho reiniciou, mas o app configurado no Autostart não abriu sozinho. "
        "Confira o Autostart (Voz V/Autostart.apk) e tente de novo.",
        "autostart",
    )
