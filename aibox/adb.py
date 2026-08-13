from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import executable_dir, is_frozen, package_dir
from .procutil import hidden_kwargs, popen_hidden, run_hidden
from .usbwin import allwinner_mode, list_present_usb, samsung_mtp_present


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str | None = None
    device: str | None = None
    transport_id: str | None = None

    @property
    def label(self) -> str:
        bits: list[str] = [self.serial, self.state]
        if self.model:
            bits.append(self.model)
        if self.device:
            bits.append(self.device)
        return " • ".join(bits)


@dataclass(frozen=True)
class InstalledApp:
    package: str
    label: str
    version_name: str | None = None
    system: bool = False

    @property
    def display(self) -> str:
        return self.label.strip() or self.package


def _looks_like_adb(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _has_usb_dlls(adb_path: Path) -> bool:
    """No Windows, USB exige AdbWinApi.dll + AdbWinUsbApi.dll ao lado do adb.exe."""
    if os.name != "nt":
        return True
    folder = adb_path.parent
    return (folder / "AdbWinApi.dll").is_file() and (folder / "AdbWinUsbApi.dll").is_file()


def _sdk_platform_tools_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SDK"):
        val = (os.environ.get(key) or "").strip()
        if val:
            roots.append(Path(val) / "platform-tools")
    local_app = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    roots.append(local_app / "Android" / "Sdk" / "platform-tools")
    roots.append(Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools")
    return roots


def _default_adb_candidates() -> list[Path]:
    cwd = Path.cwd()
    here = package_dir()
    exe_dir = executable_dir()
    candidates: list[Path] = []

    roots: list[Path] = []
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "aibox")
            roots.append(Path(meipass))
        # Layout onedir do PyInstaller 6+: _internal/
        roots.append(exe_dir / "_internal" / "aibox")
        roots.append(exe_dir / "_internal")
        roots.append(exe_dir)
    roots.extend([here, here.parent, exe_dir, cwd])
    for sdk in _sdk_platform_tools_roots():
        roots.append(sdk.parent if sdk.name == "platform-tools" else sdk)

    seen: set[str] = set()
    for base in roots:
        for folder in (
            base / "platform-tools",
            base / "plataform-tools",
            base if base.name in ("platform-tools", "plataform-tools") else None,
        ):
            if folder is None:
                continue
            for name in ("adb.exe", "adb"):
                p = folder / name
                key = str(p).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append(p)
    return candidates


def find_adb() -> str:
    env = (
        os.environ.get("AIBOX_ADB_PATH")
        or os.environ.get("CONTROID_ADB_PATH")
        or os.environ.get("ADB_PATH")
    )
    if env:
        p = Path(env)
        if _looks_like_adb(p):
            return str(p.resolve())

    bundled: list[Path] = []
    others: list[Path] = []
    for c in _default_adb_candidates():
        if _looks_like_adb(c):
            (bundled if _has_usb_dlls(c) else others).append(c)

    # App compilado: prioriza o ADB empacotado (com DLLs USB) — não o do PATH.
    if bundled:
        return str(bundled[0].resolve())

    which = shutil.which("adb") or shutil.which("adb.exe")
    if which:
        wp = Path(which)
        if _looks_like_adb(wp):
            return str(wp.resolve())

    if others:
        return str(others[0].resolve())

    raise AdbError(
        "ADB não encontrado. Reinstale o Aibox (o instalador inclui o Android Platform-Tools) "
        "ou defina AIBOX_ADB_PATH apontando para adb.exe."
    )


# VIDs extras (totens Allwinner, boxes, celulares) — o ADB padrão da Google não lista 1F3A.
_ADB_EXTRA_VIDS = (
    "0x1F3A",  # Allwinner (PROSB / totem Intelite)
    "0x18D1",  # Google
    "0x04E8",  # Samsung
    "0x22B8",  # Motorola
    "0x0BB4",  # HTC
    "0x12D1",  # Huawei
    "0x2717",  # Xiaomi
    "0x0E8D",  # MediaTek
    "0x2207",  # Rockchip
    "0x1B8E",  # Amlogic
    "0x05C6",  # Qualcomm
    "0x19D2",  # ZTE
    "0x1EBF",  # Unisoc
    "0x1782",  # Spreadtrum
    "0x0B05",  # ASUS
    "0x1004",  # LG
    "0x0FCE",  # Sony
    "0x2A70",  # OnePlus
    "0x2B0E",  # Nothing / others
    "0x2A47",
    "0x201E",  # Hisense
    "0x8087",  # Intel
)


def android_user_home() -> Path:
    override = (os.environ.get("ANDROID_USER_HOME") or "").strip()
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = Path(os.environ.get("USERPROFILE") or Path.home())
    p = base / ".android"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_adb_usb_ini() -> bool:
    """Garante adb_usb.ini com VIDs de totem. True se o arquivo mudou."""
    ini = android_user_home() / "adb_usb.ini"
    existing: list[str] = []
    if ini.is_file():
        existing = [ln.strip() for ln in ini.read_text(encoding="utf-8", errors="replace").splitlines()]
    have = {ln.lower() for ln in existing if ln and not ln.startswith("#")}
    missing = [v for v in _ADB_EXTRA_VIDS if v.lower() not in have]
    if not missing and ini.is_file():
        return False
    lines = [ln for ln in existing if ln.strip()]
    if not any(ln.strip().startswith("#") for ln in lines):
        lines.insert(0, "# Aibox — vendor IDs extras para ADB USB")
    lines.extend(missing)
    ini.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


_SERVER_RETRY_MARKERS = (
    "does not match this client",
    "cannot connect to daemon",
    "daemon not running",
    "failed to start daemon",
    "protocol fault",
    "couldn't read status",
    "cannot start server",
    "connection reset",
)


class Adb:
    def __init__(self, adb_path: str | None = None) -> None:
        self.adb_path = adb_path or find_adb()
        self._adb_dir = str(Path(self.adb_path).resolve().parent)
        self._ensure_server()

    def _adb_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = self._adb_dir + os.pathsep + env.get("PATH", "")
        env["ANDROID_USER_HOME"] = str(android_user_home())
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USERPROFILE", str(Path.home()))
        return env

    def _run_kwargs(self, *, input_bytes: bytes | None = None) -> dict:
        kwargs: dict = {
            "capture_output": True,
            "cwd": self._adb_dir,
            "env": self._adb_env(),
        }
        if input_bytes is None:
            kwargs["stdin"] = subprocess.DEVNULL
        return hidden_kwargs(**kwargs)

    def _decode_cp(self, cp: subprocess.CompletedProcess) -> str:
        out = cp.stdout or b""
        err = cp.stderr or b""
        if isinstance(out, str):
            stdout = out
        else:
            stdout = out.decode("utf-8", "replace")
        if isinstance(err, str):
            stderr = err
        else:
            stderr = err.decode("utf-8", "replace")
        return f"{stdout}\n{stderr}"

    def _needs_server_restart(self, cp: subprocess.CompletedProcess) -> bool:
        text = self._decode_cp(cp).lower()
        if "does not match this client" in text:
            return True
        if cp.returncode == 0:
            return False
        return any(m in text for m in _SERVER_RETRY_MARKERS)

    def _ensure_server(self) -> None:
        os.environ["ANDROID_USER_HOME"] = str(android_user_home())
        changed = ensure_adb_usb_ini()
        try:
            if changed:
                self._restart_server()
            else:
                self._run(["start-server"], timeout_s=25, check=False, retry_server=False)
        except AdbError:
            pass

    def _restart_server(self) -> None:
        try:
            self._run(["kill-server"], timeout_s=10, check=False, retry_server=False)
        except AdbError:
            pass
        time.sleep(0.35)
        try:
            self._run(["start-server"], timeout_s=25, check=False, retry_server=False)
        except AdbError:
            pass
        time.sleep(0.25)

    def _run(
        self,
        args: list[str],
        serial: str | None = None,
        timeout_s: int | None = 60,
        input_bytes: bytes | None = None,
        capture_binary: bool = False,
        check: bool = True,
        retry_server: bool = True,
    ) -> subprocess.CompletedProcess:
        cmd = [self.adb_path]
        if serial:
            cmd += ["-s", serial]
        cmd += args

        run_kw = self._run_kwargs(input_bytes=input_bytes)
        try:
            cp = subprocess.run(
                cmd,
                input=input_bytes,
                timeout=timeout_s,
                **run_kw,
            )
        except FileNotFoundError as e:
            raise AdbError(f"ADB inválido: {self.adb_path}") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"Timeout executando: {shlex.join(cmd)}") from e

        if retry_server and self._needs_server_restart(cp):
            self._restart_server()
            return self._run(
                args,
                serial=serial,
                timeout_s=timeout_s,
                input_bytes=input_bytes,
                capture_binary=capture_binary,
                check=check,
                retry_server=False,
            )

        if check and cp.returncode != 0:
            stderr = (cp.stderr or b"").decode("utf-8", "replace").strip() if isinstance(cp.stderr, (bytes, bytearray)) else str(cp.stderr or "").strip()
            stdout = (cp.stdout or b"").decode("utf-8", "replace").strip() if isinstance(cp.stdout, (bytes, bytearray)) else str(cp.stdout or "").strip()
            msg = stderr or stdout or f"Erro executando: {shlex.join(cmd)}"
            raise AdbError(msg)

        if capture_binary:
            return cp

        stdout = (cp.stdout or b"").decode("utf-8", "replace") if isinstance(cp.stdout, (bytes, bytearray)) else str(cp.stdout or "")
        stderr = (cp.stderr or b"").decode("utf-8", "replace") if isinstance(cp.stderr, (bytes, bytearray)) else str(cp.stderr or "")
        return subprocess.CompletedProcess(cmd, cp.returncode, stdout, stderr)

    def version(self) -> str:
        cp = self._run(["version"], timeout_s=10)
        return str(cp.stdout).strip()

    def _parse_devices(self, stdout: str) -> list[AdbDevice]:
        devices: list[AdbDevice] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            rest = parts[2:]
            model = None
            device = None
            transport_id = None
            for r in rest:
                if r.startswith("model:"):
                    model = r.split(":", 1)[1]
                elif r.startswith("device:"):
                    device = r.split(":", 1)[1]
                elif r.startswith("transport_id:"):
                    transport_id = r.split(":", 1)[1]
            devices.append(AdbDevice(serial=serial, state=state, model=model, device=device, transport_id=transport_id))
        return devices

    def list_devices(self) -> list[AdbDevice]:
        cp = self._run(["devices", "-l"], timeout_s=15, check=False)
        devices = self._parse_devices(str(cp.stdout))
        if not devices:
            time.sleep(0.5)
            cp = self._run(["devices", "-l"], timeout_s=15, check=False)
            devices = self._parse_devices(str(cp.stdout))
        if not devices:
            try:
                mode = allwinner_mode()
            except Exception:
                mode = None
            if mode in ("adb", "composite"):
                try:
                    self._restart_server()
                    cp = self._run(["devices", "-l"], timeout_s=15, check=False)
                    devices = self._parse_devices(str(cp.stdout))
                except AdbError:
                    pass
        if cp.returncode != 0 and not devices:
            stderr = str(cp.stderr or "").strip()
            stdout = str(cp.stdout or "").strip()
            msg = stderr or stdout or "Falha ao listar dispositivos ADB."
            raise AdbError(msg)
        return devices

    def explain_empty_devices(self) -> str:
        """Texto para a UI quando `adb devices` está vazio."""
        try:
            present = list_present_usb()
        except Exception:
            present = []
        mode = allwinner_mode(present)
        if mode == "storage":
            return (
                "Totem conectado em modo armazenamento (PROSB5000 / Allwinner), sem ADB.\n"
                "No aparelho: ative Opções do desenvolvedor → Depuração USB "
                "e use um cabo de dados. O Windows deve mostrar «ADB Interface».\n"
                "Depois clique em Atualizar."
            )
        if mode == "composite":
            return (
                "Totem Allwinner detectado, mas a interface ADB ainda não apareceu.\n"
                "Confirme a Depuração USB, desconecte e reconecte o cabo e clique em Atualizar."
            )
        if mode == "adb":
            return (
                "A interface ADB do totem está no Windows, mas o adb não listou o aparelho.\n"
                "Desconecte e reconecte o USB, aceite a autorização RSA na tela do totem e clique em Atualizar."
            )
        if samsung_mtp_present(present):
            return (
                "Um Samsung está no PC só como armazenamento (MTP), sem ADB.\n"
                "Ative a Depuração USB e o modo Transferir arquivos, depois Atualizar."
            )
        if present:
            vids = sorted({f"{d.vid}:{d.pid}" for d in present})
            sample = ", ".join(vids[:8])
            return (
                "Nenhum dispositivo ADB. USB conectado: "
                f"{sample}.\n"
                "Ative a Depuração USB (cabo de dados) ou use Conectar Wi‑Fi."
            )
        return (
            "Nenhum dispositivo ADB e nenhum totem USB detectado.\n"
            "Conecte o cabo, ative a Depuração USB ou informe IP:PORTA no Wi‑Fi."
        )

    def connect(self, address: str) -> str:
        cp = self._run(["connect", address], timeout_s=25)
        msg = str(cp.stdout).strip()
        low = msg.lower()
        if (
            "failed to connect" in low
            or "unable to connect" in low
            or "cannot connect" in low
            or "connection refused" in low
            or "no route to host" in low
        ):
            raise AdbError(msg or f"Falha ao conectar em {address}.")
        return msg

    def pair(self, address: str, pairing_code: str) -> str:
        pairing_code = (pairing_code or "").strip()
        if not pairing_code:
            raise AdbError("Código de pareamento inválido.")
        cp = self._run(["pair", address, pairing_code], timeout_s=25)
        msg = str(cp.stdout).strip()
        low = msg.lower()
        if "error:" in low or "failed" in low or "failure" in low:
            raise AdbError(msg or f"Falha ao parear com {address}.")
        return msg

    def disconnect(self, address: str | None = None) -> str:
        args = ["disconnect"]
        if address:
            args.append(address)
        cp = self._run(args, timeout_s=20)
        return str(cp.stdout).strip()

    def screenshot_png(self, serial: str, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        cmd = [self.adb_path, "-s", serial, "exec-out", "screencap", "-p"]
        try:
            p = popen_hidden(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                cwd=self._adb_dir,
                env=self._adb_env(),
            )
        except FileNotFoundError as e:
            raise AdbError(f"ADB inválido: {self.adb_path}") from e

        try:
            assert p.stdout is not None
            with tmp.open("wb") as f:
                while True:
                    chunk = p.stdout.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
            p.wait(timeout=30)
        except subprocess.TimeoutExpired as e:
            try:
                p.kill()
            except Exception:
                pass
            raise AdbError(f"Timeout executando: {shlex.join(cmd)}") from e
        finally:
            try:
                if p.stdout:
                    p.stdout.close()
            except Exception:
                pass

        stderr = b""
        try:
            if p.stderr:
                stderr = p.stderr.read() or b""
        finally:
            try:
                if p.stderr:
                    p.stderr.close()
            except Exception:
                pass

        if p.returncode != 0:
            msg = (stderr or b"").decode("utf-8", "replace").strip() or f"Erro executando: {shlex.join(cmd)}"
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise AdbError(msg)

        try:
            if not tmp.exists() or tmp.stat().st_size <= 0:
                raise AdbError("Falha ao capturar print: saída vazia do ADB.")
        except OSError:
            raise AdbError("Falha ao capturar print: não foi possível validar o arquivo gerado.")

        tmp.replace(out_path)
        return out_path

    def screencap_png_bytes(self, serial: str, timeout_s: int = 10) -> bytes:
        cmd = [self.adb_path, "-s", serial, "exec-out", "screencap", "-p"]
        try:
            p = popen_hidden(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                cwd=self._adb_dir,
                env=self._adb_env(),
            )
        except FileNotFoundError as e:
            raise AdbError(f"ADB inválido: {self.adb_path}") from e

        out = bytearray()
        try:
            assert p.stdout is not None
            while True:
                chunk = p.stdout.read(1024 * 64)
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > 25 * 1024 * 1024:
                    raise AdbError("Preview muito grande: captura excedeu 25MB.")
            p.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            try:
                p.kill()
            except Exception:
                pass
            raise AdbError(f"Timeout executando: {shlex.join(cmd)}") from e
        finally:
            try:
                if p.stdout:
                    p.stdout.close()
            except Exception:
                pass

        stderr = b""
        try:
            if p.stderr:
                stderr = p.stderr.read() or b""
        finally:
            try:
                if p.stderr:
                    p.stderr.close()
            except Exception:
                pass

        if p.returncode != 0:
            msg = (stderr or b"").decode("utf-8", "replace").strip() or f"Erro executando: {shlex.join(cmd)}"
            raise AdbError(msg)

        data = bytes(out)
        if not data:
            raise AdbError("Preview: sem dados.")
        return data

    def get_density(self, serial: str) -> str:
        return self.shell(serial, "wm density", timeout_s=20).strip()

    def set_density(self, serial: str, dpi: int) -> str:
        dpi = int(dpi)
        if dpi < 72 or dpi > 640:
            raise AdbError("DPI inválido. Use um valor entre 72 e 640.")
        self.shell(serial, f"wm density {dpi}", timeout_s=20)
        current = self.get_density(serial)
        return f"DPI aplicado: {dpi}\nAtual no dispositivo:\n{current}".strip()

    def reset_density(self, serial: str) -> str:
        self.shell(serial, "wm density reset", timeout_s=20)
        current = self.get_density(serial)
        return f"DPI restaurado ao padrão do aparelho.\nAtual no dispositivo:\n{current}".strip()

    def install_apk(
        self,
        serial: str,
        apk_path: Path,
        grant_all: bool = True,
        replace: bool = True,
        allow_downgrade: bool = False,
    ) -> str:
        if not apk_path.exists():
            raise AdbError(f"APK não encontrado: {apk_path}")
        args = ["install"]
        if replace:
            args.append("-r")
        if allow_downgrade:
            args.append("-d")
        if grant_all:
            args.append("-g")
        args.append(str(apk_path))
        cp = self._run(args, serial=serial, timeout_s=300)
        msg = str(cp.stdout).strip()
        low = msg.lower()
        if "failure" in low or "failed" in low or "error:" in low:
            raise AdbError(msg or "Falha ao instalar APK.")
        return msg or "Success"

    def install_tts_apk(self, serial: str, apk_path: Path) -> str:
        """Instala Google TTS e confirma que o pacote ficou disponível."""
        import time

        notes: list[str] = []
        try:
            notes.append(self.install_apk(serial, apk_path, allow_downgrade=True))
        except AdbError as e:
            err = str(e).lower()
            notes.append(f"1ª tentativa: {e}")
            # Assinatura/sistema diferente: remove a cópia do usuário e reinstala
            if any(
                k in err
                for k in (
                    "update_incompatible",
                    "signatures",
                    "inconsistent certificates",
                    "version downgrade",
                    "already exists",
                )
            ):
                self.shell_try(
                    serial,
                    "pm uninstall --user 0 com.google.android.tts",
                    timeout_s=30,
                )
                try:
                    self._run(
                        ["uninstall", "com.google.android.tts"],
                        serial=serial,
                        timeout_s=60,
                    )
                except AdbError:
                    pass
                notes.append(
                    self.install_apk(serial, apk_path, allow_downgrade=True)
                )
            else:
                raise AdbError("; ".join(notes)) from e

        # Aguarda o pacote aparecer (pm pode atrasar um pouco)
        engine = None
        for _ in range(10):
            engine = self._resolve_tts_engine_package(serial)
            if engine:
                break
            time.sleep(0.4)
        if not engine:
            raise AdbError(
                "APK da Síntese de Voz não ficou instalado "
                "(com.google.android.tts ausente). "
                + " | ".join(notes)
            )
        self.shell_try(serial, f"pm enable {engine}", timeout_s=6)
        return f"{' | '.join(notes)} • motor={engine}"

    def package_name_from_apk(self, apk_path: Path) -> str | None:
        """Lê o package name do .apk (aapt no host; fallback pelo manifest zip)."""
        if not apk_path.exists():
            return None
        aapt = self._find_aapt()
        if aapt:
            try:
                cp = run_hidden(
                    [aapt, "dump", "badging", str(apk_path)],
                    capture_output=True,
                    timeout=30,
                )
                text = (cp.stdout or b"").decode("utf-8", "replace")
                m = re.search(r"package:\s*name='([^']+)'", text)
                if m:
                    return m.group(1).strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            import zipfile

            with zipfile.ZipFile(apk_path) as zf:
                raw = zf.read("AndroidManifest.xml")
        except Exception:
            return None
        # String pool UTF-16LE (ASCII) — procura algo tipo com.empresa.app
        chunks = re.findall(rb"(?:[\x20-\x7e]\x00){6,80}", raw)
        candidates: list[str] = []
        for ch in chunks:
            try:
                s = ch.decode("utf-16le", errors="ignore").strip("\x00")
            except Exception:
                continue
            if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*){1,8}", s):
                if s.count(".") >= 1 and not s.startswith("android."):
                    candidates.append(s)
        if not candidates:
            return None
        # Prefere o mais “parecido” com app (não permissões)
        for s in candidates:
            if "permission" in s.lower() or "intent" in s.lower():
                continue
            return s
        return candidates[0]

    def launchable_component(self, serial: str, package: str) -> str | None:
        """Retorna package/activity lançável (LEANBACK_LAUNCHER ou LAUNCHER)."""
        package = (package or "").strip()
        if not package:
            return None
        for category in (
            "android.intent.category.LEANBACK_LAUNCHER",
            "android.intent.category.LAUNCHER",
        ):
            out = self.shell_lax(
                serial,
                f"cmd package resolve-activity --brief -c {category} {package}",
                timeout_s=15,
            )
            comp = self._parse_component_from_resolve(out, package)
            if comp:
                return comp
        # Fallback dumpsys
        dump = self.shell_lax(
            serial,
            f"dumpsys package {package} | grep -E 'android.intent.action.MAIN|LEANBACK_LAUNCHER|LAUNCHER' -A2 | head -n 40",
            timeout_s=20,
        )
        m = re.search(
            rf"({re.escape(package)}/[a-zA-Z0-9_$.]+)",
            dump or "",
        )
        return m.group(1) if m else None

    @staticmethod
    def _parse_component_from_resolve(out: str, package: str) -> str | None:
        for line in reversed((out or "").splitlines()):
            line = line.strip()
            if not line or line.startswith("priority") or "No activity" in line:
                continue
            if "/" in line and (line.startswith(package) or package in line):
                # pode vir "package/.Activity" ou nome completo
                if line.count("/") == 1:
                    return line
                m = re.search(rf"({re.escape(package)}/[^\s]+)", line)
                if m:
                    return m.group(1)
        return None

    def force_stop_autostart_apps(self, serial: str) -> None:
        """Fecha Autostart em primeiro plano."""
        self.shell_try(
            serial,
            "am force-stop com.autostart; "
            "am force-stop com.droidlogic.app.Autostart; "
            "am force-stop com.android.autostart; "
            "am force-stop com.softwinner.autostart; "
            "am force-stop com.android.rockchip.autostart",
            timeout_s=3,
        )

    def list_autostart_packages(self, serial: str) -> list[str]:
        pkgs = [
            "com.autostart",
            "com.droidlogic.app.Autostart",
            "com.android.autostart",
            "com.softwinner.autostart",
            "com.android.rockchip.autostart",
        ]
        listed = self.shell_try(serial, "pm list packages | grep -i autostart", timeout_s=4) or ""
        for line in listed.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkg = line.split(":", 1)[-1].strip()
                if pkg and pkg not in pkgs:
                    pkgs.insert(0, pkg)
        return pkgs

    def set_autostart_apps_enabled(self, serial: str, enabled: bool) -> None:
        """Desativa/ativa o Autostart (pm disable-user). force-stop sozinho não basta."""
        pkgs = self.list_autostart_packages(serial)
        if enabled:
            for pkg in pkgs:
                self.shell_try(serial, f"pm enable {pkg}", timeout_s=4)
        else:
            self.force_stop_autostart_apps(serial)
            for pkg in pkgs:
                self.shell_try(serial, f"pm disable-user --user 0 {pkg}", timeout_s=4)
                self.shell_try(serial, f"pm disable {pkg}", timeout_s=3)
                self.shell_try(serial, f"am force-stop {pkg}", timeout_s=2)

    def _ensure_autostart_dead(self, serial: str) -> None:
        """Garante que Autostart não está na frente (entre passos da TTS)."""
        self.force_stop_autostart_apps(serial)

    def shell_try(self, serial: str, cmd: str, timeout_s: int = 4) -> str | None:
        """Como shell_lax, mas em timeout/erro devolve None (não derruba o fluxo)."""
        try:
            return self.shell_lax(serial, cmd, timeout_s=timeout_s)
        except AdbError:
            return None

    def _tap_and_confirm(self, serial: str, x: int, y: int) -> bool:
        """Tap (+ CENTER best-effort). Tap sozinho já basta em várias UIs Leanback."""
        ok = self.shell_try(serial, f"input tap {x} {y}", timeout_s=3) is not None
        if ok:
            self.shell_try(serial, "input keyevent 23", timeout_s=2)
        return ok

    def _tap_only(self, serial: str, x: int, y: int) -> bool:
        return self.shell_try(serial, f"input tap {x} {y}", timeout_s=3) is not None

    def close_settings_and_tts_ui(self, serial: str) -> None:
        """Fecha telas de Configurações/TTS antes de abrir o Autostart."""
        self.shell_try(
            serial,
            "am force-stop com.android.tv.settings; "
            "am force-stop com.android.settings; "
            "am force-stop com.google.android.tts; "
            "input keyevent 3",
            timeout_s=5,
        )

    @staticmethod
    def _fq_activity_class(package: str, activity: str) -> str:
        """Converte `.SplashActivity` → `package.SplashActivity` (firmware exige FQCN)."""
        act = (activity or "").strip()
        pkg = (package or "").strip()
        if not act:
            return act
        if act.startswith("."):
            return f"{pkg}{act}"
        if pkg and act.startswith(f"{pkg}."):
            return act
        if "/" in act:
            # package/.Activity ou package/Activity
            _, rhs = act.split("/", 1)
            return Adb._fq_activity_class(pkg, rhs)
        if "." not in act and pkg:
            return f"{pkg}.{act}"
        return act

    def configure_boot_autostart(
        self,
        serial: str,
        packages: list[str],
        *,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Configura Totem/Painel para iniciar no boot.

        1) appops / whitelist
        2) props persist.sys.boot* com activity em nome completo (FQCN)
        3) tenta HOME/launcher (só funciona se o app tiver CATEGORY_HOME)
        4) se Autostart.apk estiver instalado: liga e marca os apps na UI
        """
        labels = labels or {}
        pkgs = []
        seen: set[str] = set()
        for p in packages:
            p = (p or "").strip()
            if not p or p in seen:
                continue
            # Nunca trata o próprio Autostart como app de boot
            if "autostart" in p.lower():
                continue
            seen.add(p)
            pkgs.append(p)
        if not pkgs:
            return "Autoinício: nenhum pacote Totem/Painel para configurar."

        lines: list[str] = ["=== Autoinício no boot ==="]

        components: dict[str, str] = {}
        for pkg in pkgs:
            self.shell_try(serial, f"pm enable {pkg}", timeout_s=6)
            for op in (
                "APP_AUTO_START",
                "RUN_IN_BACKGROUND",
                "RUN_ANY_IN_BACKGROUND",
                "START_FOREGROUND",
            ):
                self.shell_try(serial, f"appops set {pkg} {op} allow", timeout_s=4)
            self.shell_try(serial, f"dumpsys deviceidle whitelist +{pkg}", timeout_s=4)
            comp = self.launchable_component(serial, pkg)
            if comp:
                components[pkg] = comp
                lines.append(f"• {pkg} → {comp}")
            else:
                lines.append(f"• {pkg} → (activity lançável não encontrada)")

        primary = pkgs[0]
        primary_label = labels.get(primary, primary)
        primary_comp = components.get(primary)
        primary_activity_raw = None
        if primary_comp and "/" in primary_comp:
            primary_activity_raw = primary_comp.split("/", 1)[1]
        primary_activity = self._fq_activity_class(primary, primary_activity_raw or "")
        # Componente com FQCN (melhor para props/firmware)
        primary_comp_fq = (
            f"{primary}/{primary_activity}" if primary_activity else primary_comp
        )

        prop_ok = 0
        if primary_activity and primary_comp_fq:
            # KickPI/Allwinner: bootAppClass deve ser FQCN (não ".SplashActivity")
            prop_cmds = [
                f"setprop persist.sys.bootAppPack {primary}",
                f"setprop persist.sys.bootAppClass {primary_activity}",
                f"setprop persist.sys.bootpackage {primary}",
                f"setprop persist.sys.bootactivity {primary_activity}",
                f"setprop persist.sys.boot_app {primary_comp_fq}",
                f"setprop persist.sys.bootpkg {primary}",
                f"setprop persist.sys.boot_activity {primary_activity}",
                f"setprop persist.vendor.bootpackage {primary}",
                f"setprop persist.vendor.bootactivity {primary_activity}",
                f"setprop persist.sys.bootapp {primary}",
                f"setprop persist.sys.custom_launcher {primary_comp_fq}",
            ]
            # Também grava forma relativa (alguns firmwares legados)
            if primary_activity_raw and primary_activity_raw != primary_activity:
                prop_cmds.extend(
                    [
                        f"setprop persist.sys.bootAppClassRel {primary_activity_raw}",
                    ]
                )
            for cmd in prop_cmds:
                out = self.shell_try(serial, cmd, timeout_s=4) or ""
                low = out.lower()
                if "denied" in low or "error" in low or "failed" in low:
                    continue
                prop_ok += 1

            check_class = (
                self.shell_try(serial, "getprop persist.sys.bootAppClass", timeout_s=4)
                or ""
            ).strip()
            check_pack = (
                self.shell_try(serial, "getprop persist.sys.bootAppPack", timeout_s=4)
                or ""
            ).strip()
            if check_pack == primary and primary_activity in check_class:
                lines.append(
                    f"Firmware boot: OK — «{primary_label}» "
                    f"({primary} / {primary_activity})."
                )
            elif prop_ok:
                lines.append(
                    f"Firmware boot: props enviadas para «{primary_label}» "
                    f"({prop_ok} comandos, class={primary_activity}). Valide após reiniciar."
                )
            else:
                lines.append(
                    "Firmware boot: setprop sem efeito visível "
                    "(algumas imagens bloqueiam props sem root)."
                )
        else:
            lines.append("Firmware boot: sem activity do app principal — props não aplicadas.")

        # HOME só funciona se o APK declarar CATEGORY_HOME (Painel normalmente não declara)
        if primary_comp_fq:
            home = self.shell_try(
                serial,
                f"cmd package set-home-activity --user 0 {primary_comp_fq}",
                timeout_s=10,
            ) or ""
            home2 = self.shell_try(
                serial,
                f"cmd package set-home-activity {primary_comp_fq}",
                timeout_s=10,
            ) or ""
            home_all = f"{home}\n{home2}".lower()
            if "error" in home_all or "exception" in home_all or "failed" in home_all:
                lines.append(
                    "Launcher/home: app sem CATEGORY_HOME "
                    "(esperado) — autoinício depende de firmware/Autostart."
                )
            else:
                lines.append(f"Launcher/home: «{primary_label}» ({primary_comp_fq}).")

        # Caminho confiável neste Mini PC: Autostart.apk
        auto_pkg = self._resolve_autostart_package(serial)
        if auto_pkg:
            self.set_autostart_apps_enabled(serial, True)
            lines.append(f"APK Autostart: {auto_pkg} (habilitado)")
            lines.append(
                self._configure_autostart_app_ui(
                    serial, auto_pkg, pkgs, labels=labels
                )
            )
        else:
            lines.append(
                "APK Autostart: não instalado — neste firmware as props sozinhas "
                "costumam NÃO abrir o app. Instale Autostart.apk e rode de novo."
            )

        if len(pkgs) > 1:
            extra = ", ".join(labels.get(p, p) for p in pkgs[1:])
            lines.append(
                f"Apps alvo: principal «{primary_label}»; também: {extra}."
            )

        lines.append("Reinicie o Mini PC para validar o autoinício.")
        return "\n".join(lines)

    def _resolve_autostart_package(self, serial: str) -> str | None:
        out = self.shell_lax(serial, "pm list packages | grep -i autostart", timeout_s=15)
        preferred = (
            "com.droidlogic.app.Autostart",
            "com.android.autostart",
            "com.autostart",
        )
        found: list[str] = []
        for line in (out or "").splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            pkg = line.split(":", 1)[-1].strip()
            if pkg:
                found.append(pkg)
        for p in preferred:
            if p in found:
                return p
        return found[0] if found else None

    def _configure_autostart_app_ui(
        self,
        serial: str,
        auto_pkg: str,
        packages: list[str],
        *,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Fluxo Autostart persistente:

        1) Abrir app e fechar overlays (Play login / update)
        2) ToggleButton OFF → ON
        3) ADD → tocar a linha do app (mesmo sem clickable)
        4) Fechar com BACK (nunca force-stop — senão não grava)
        5) Reabrir e verificar ON + apps na lista
        """
        import time

        labels = labels or {}
        auto_pkgs = set(self.list_autostart_packages(serial))
        auto_pkgs.add(auto_pkg)
        targets = [p for p in packages if p not in auto_pkgs]

        # Não use force-stop no Autostart durante a config
        self.shell_try(
            serial,
            "am force-stop com.android.tv.settings; "
            "am force-stop com.android.settings; "
            "input keyevent 3",
            timeout_s=4,
        )
        time.sleep(0.08)

        self.shell_try(serial, f"pm enable {auto_pkg}", timeout_s=3)
        if not self._open_autostart_main(serial, auto_pkg):
            return "UI Autostart: não abriu — configure manualmente."

        notes: list[str] = []
        self._ui_dismiss_autostart_overlays(serial)
        time.sleep(0.1)

        if self._ui_turn_on_autostart(serial):
            notes.append("Auto startup=ON")
        else:
            notes.append("Auto startup: falhou ao ligar")
        time.sleep(0.08)

        enabled = 0
        for pkg in targets:
            nice = (labels.get(pkg) or "").strip()
            search_labels: list[str] = []
            if nice:
                search_labels.extend(
                    [
                        nice,
                        nice.replace(" ", ""),
                        # UI do Autostart costuma mostrar "Nome - Painel/Totem"
                        f"{nice} - Painel",
                        f"{nice} - Totem",
                        f"{nice} - Painel Cirurgico",
                    ]
                )
            search_labels.extend(
                [
                    "Atendimento Inteligente - Painel",
                    "Atendimento Inteligente - Totem",
                    "Painel Cirurgico",
                    "Totem Interativo",
                    pkg.rsplit(".", 1)[-1],
                ]
            )
            for extra in (
                "Atendimento Inteligente",
                "Atendimento",
                "Painel",
                "Totem",
                "Aiclass",
                "Ainurse",
                "Aifit",
                "Upzz",
            ):
                if nice and extra.lower() in nice.lower():
                    search_labels.append(extra)
                elif not nice:
                    search_labels.append(extra)

            # Já está na lista principal?
            if self._ui_autostart_has_app(serial, tuple(dict.fromkeys(search_labels))):
                enabled += 1
                continue

            if not self._ui_open_applications_add(serial):
                notes.append(f"ADD falhou ({nice or pkg})")
                continue
            time.sleep(0.12)

            if self._ui_pick_autostart_app_from_list(
                serial, tuple(dict.fromkeys(search_labels))
            ):
                enabled += 1
                time.sleep(0.12)
            else:
                self.shell_try(serial, "input keyevent 4", timeout_s=2)
                time.sleep(0.08)
                notes.append(f"não achou na lista: {nice or pkg}")

        notes.append(f"apps na lista: {enabled}/{len(targets)}")

        # Confirma na tela atual (sem reabrir — economiza ~20–40s)
        xml = self._dump_ui_xml(serial) or ""
        on_ok = self._ui_autostart_is_on(xml)
        notes.append(f"confirma: startup={'ON' if on_ok else 'OFF'}")

        # Grava preferências: BACK/HOME (nunca force-stop)
        self.shell_try(serial, "input keyevent 4; input keyevent 3", timeout_s=3)
        time.sleep(0.12)
        return "UI Autostart: " + "; ".join(notes)

    def _open_autostart_main(self, serial: str, auto_pkg: str) -> bool:
        import time

        comp = self.launchable_component(serial, auto_pkg)
        if comp:
            try:
                out = self.shell_combined(serial, f"am start -n {comp}", timeout_s=8)
                if self._am_start_succeeded(out) or "starting:" in (out or "").lower():
                    time.sleep(0.35)
                    return True
            except AdbError:
                pass
        try:
            out = self.shell_combined(
                serial,
                f"monkey -p {auto_pkg} -c android.intent.category.LAUNCHER 1",
                timeout_s=8,
            )
            ok = "events injected" in (out or "").lower() or "starting" in (out or "").lower()
            if ok:
                time.sleep(0.35)
            return ok
        except AdbError:
            return False

    def _ui_dismiss_autostart_overlays(self, serial: str) -> None:
        """Fecha Play login / update que cobrem a tela principal do Autostart."""
        import time

        for _ in range(3):
            xml = (self._dump_ui_xml(serial) or "").lower()
            if "auto startup" in xml and (
                "togglebutton" in xml or 'text="off"' in xml or 'text="on"' in xml
            ):
                self._ui_dismiss_autostart_update_dialog(serial)
                return
            if any(
                k in xml
                for k in (
                    "fazer login",
                    "faça login",
                    "faca login",
                    "sign in",
                    "google play",
                    "melhores apps",
                )
            ):
                self.shell_try(serial, "input keyevent 4", timeout_s=2)
                time.sleep(0.12)
                continue
            if self._ui_dismiss_autostart_update_dialog(serial):
                time.sleep(0.1)
                continue
            self.shell_try(serial, "input keyevent 4", timeout_s=2)
            time.sleep(0.1)

    @staticmethod
    def _ui_autostart_is_on(xml: str) -> bool:
        low = (xml or "").lower()
        if 'text="on"' in low or "text='on'" in low:
            return 'text="off"' not in low and "text='off'" not in low
        return 'class="android.widget.togglebutton"' in low and 'checked="true"' in low

    def _ui_autostart_has_app(self, serial: str, labels: tuple[str, ...]) -> bool:
        xml = (self._dump_ui_xml(serial) or "").lower()
        # Na tela principal, apps adicionados aparecem com um "X" ao lado
        if "auto startup" not in xml and "applications" not in xml:
            return False
        for lab in labels:
            lab = (lab or "").strip().lower()
            if lab and lab in xml:
                return True
        return False

    def _ui_tap_label_anywhere(
        self,
        serial: str,
        labels: tuple[str, ...],
        *,
        prefer_left: bool = False,
    ) -> bool:
        """Toca o bounds do texto mesmo se clickable=false (lista ADD do Autostart)."""
        import xml.etree.ElementTree as ET

        xml = self._dump_ui_xml(serial)
        if not (xml or "").strip().startswith("<"):
            return False
        try:
            root = ET.fromstring(xml[xml.index("<") :])
        except ET.ParseError:
            return False

        wanted = tuple(x.strip().lower() for x in labels if x.strip())
        best: tuple[int, int, int] | None = None
        for node in root.iter("node"):
            texts = [
                (node.get("text") or "").strip(),
                (node.get("content-desc") or "").strip(),
            ]
            score = -1
            for i, lab in enumerate(wanted):
                for t in texts:
                    tl = t.lower()
                    if not tl:
                        continue
                    if tl == lab:
                        score = max(score, 1000 - i)
                    elif lab in tl or tl in lab:
                        score = max(score, 500 - i)
            if score < 0:
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (node.get("bounds") or "").strip())
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if prefer_left:
                x, y = max(8, x1 - 24), (y1 + y2) // 2
            else:
                x, y = (x1 + x2) // 2, (y1 + y2) // 2
            if best is None or score > best[0]:
                best = (score, x, y)
        if best is None:
            return False
        return self._tap_only(serial, best[1], best[2])

    def _ui_pick_autostart_app_from_list(self, serial: str, labels: tuple[str, ...]) -> bool:
        """Na tela ADD: seleciona o app pela linha (ícone/texto)."""
        import time

        for _ in range(6):
            xml = (self._dump_ui_xml(serial) or "").lower()
            if "auto startup" in xml and any((lab or "").lower() in xml for lab in labels if lab):
                if 'text="add"' in xml or "text='add'" in xml:
                    return True
            if self._ui_tap_label_anywhere(serial, labels, prefer_left=True):
                time.sleep(0.18)
                xml2 = (self._dump_ui_xml(serial) or "").lower()
                if "auto startup" in xml2 and any(
                    (lab or "").lower() in xml2 for lab in labels if lab
                ):
                    return True
            self.shell_try(
                serial,
                "input keyevent 20; input keyevent 20; input keyevent 20; "
                "input keyevent 20; input keyevent 20; input keyevent 20",
                timeout_s=2,
            )
            time.sleep(0.04)
        return False

    def _ui_turn_on_autostart(self, serial: str) -> bool:
        """Liga o ToggleButton OFF → ON."""
        import time

        xml = self._dump_ui_xml(serial) or ""
        if self._ui_autostart_is_on(xml):
            return True

        if self._ui_tap_label_anywhere(serial, ("OFF", "Off"), prefer_left=False):
            time.sleep(0.12)
            return self._ui_autostart_is_on(self._dump_ui_xml(serial) or "")

        if self._ui_navigate_step(
            serial, ("OFF", "Off"), scrolls=0, pause=0.05, guard_autostart=False
        ):
            time.sleep(0.1)
            return self._ui_autostart_is_on(self._dump_ui_xml(serial) or "")
        return False

    def _ui_open_applications_add(self, serial: str) -> bool:
        """Abre o botão ADD da tela principal."""
        import time

        xml = (self._dump_ui_xml(serial) or "").lower()
        if "show all applications" in xml:
            return True

        if self._ui_tap_label_anywhere(serial, ("ADD", "Add", "Adicionar"), prefer_left=False):
            time.sleep(0.18)
            xml2 = (self._dump_ui_xml(serial) or "").lower()
            return "show all applications" in xml2 or any(
                k in xml2
                for k in ("facebook", "netflix", "spotify", "atendimento", "painel", "totem")
            )

        return self._ui_navigate_step(
            serial,
            ("ADD", "Add", "Adicionar"),
            scrolls=1,
            pause=0.08,
            guard_autostart=False,
        )

    def _ui_dismiss_autostart_update_dialog(self, serial: str) -> bool:
        """Fecha o popup que sobe ao abrir o Autostart.

        Foco inicia em «PROCURAR ATUALIZAÇÕES». Regra rígida:
        - Nunca enviar CENTER enquanto o foco estiver em Procurar
        - Só RIGHT, esperar, depois tap no botão OK (ou CENTER se OK estiver focado)
        """
        import time
        import xml.etree.ElementTree as ET

        def _dialog_open(xml: str) -> bool:
            low = (xml or "").lower()
            has_procurar = any(
                t in low
                for t in (
                    "procurar atualiza",
                    "buscar atualiza",
                    "procurar atualizações",
                    "buscar atualizações",
                    "check for update",
                )
            )
            has_ok = bool(re.search(r'(?:text|content-desc)="ok"', low))
            return has_procurar and has_ok

        def _parse_root(xml: str):
            if not (xml or "").strip().startswith("<"):
                return None
            try:
                return ET.fromstring(xml[xml.index("<") :])
            except ET.ParseError:
                return None

        def _ok_xy(xml: str) -> tuple[int, int] | None:
            root = _parse_root(xml)
            if root is None:
                return None
            best: tuple[int, int, int] | None = None
            for node in root.iter("node"):
                texts = []
                for attr in ("text", "content-desc"):
                    v = (node.get(attr) or "").strip()
                    if v:
                        texts.append(v)
                for c in list(node):
                    v = (c.get("text") or "").strip()
                    if v:
                        texts.append(v)
                if not any(t.strip().lower() == "ok" for t in texts):
                    continue
                blob = " ".join(texts).lower()
                if any(f in blob for f in ("procurar", "buscar", "atualiza", "update")):
                    continue
                center = self._bounds_center(node.get("bounds") or "")
                if center is None:
                    continue
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (node.get("bounds") or "").strip())
                score = center[0]
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    if (x2 - x1) > 900 or (y2 - y1) > 240:
                        continue
                    score = x2
                if best is None or score > best[0]:
                    best = (score, center[0], center[1])
            return (best[1], best[2]) if best else None

        def _focus_blob(xml: str) -> str:
            root = _parse_root(xml)
            if root is None:
                return ""
            parts: list[str] = []
            for node in root.iter("node"):
                if node.get("focused") == "true" or node.get("selected") == "true":
                    parts.append((node.get("text") or "").lower())
                    parts.append((node.get("content-desc") or "").lower())
            return " ".join(parts)

        # Popup costuma já estar lá; 2 dumps bastam para decidir
        xml = self._dump_ui_xml(serial)
        if not _dialog_open(xml):
            time.sleep(0.15)
            xml = self._dump_ui_xml(serial)
            if not _dialog_open(xml):
                return False

        for attempt in range(5):
            xml = self._dump_ui_xml(serial)
            if not _dialog_open(xml):
                return True

            focus = _focus_blob(xml)
            if any(t in focus for t in ("procurar", "buscar", "atualiza", "update")):
                self.shell_try(serial, "input keyevent 22", timeout_s=2)
                time.sleep(0.18)
                continue

            if re.search(r"(^|\s)ok(\s|$)", focus):
                self.shell_try(serial, "input keyevent 23", timeout_s=2)
                time.sleep(0.18)
                continue

            if attempt % 2 == 0:
                self.shell_try(serial, "input keyevent 22", timeout_s=2)
                time.sleep(0.12)
            pos = _ok_xy(xml)
            if pos is not None:
                self._tap_only(serial, pos[0], pos[1])
                time.sleep(0.2)

        return not _dialog_open(self._dump_ui_xml(serial) or "")

    def configure_tts_pt_br_voice_v(self, serial: str) -> str:
        """Configura Síntese de Voz (pt-BR / Voz V) sem navegar Configurações na tela.

        Equivalente funcional a:
          motor Google TTS + locale pt-BR + dados de voz pt-BR + Voz V

        Estratégia silenciosa:
          1) settings secure (motor/idioma) — sem UI
          2) brilho/animações zerados — procedimento invisível
          3) intent direto VoiceDataInstallActivity (não percorre menus)
          4) só na activity do TTS: Português (Brasil) → Voz V
          5) reaplicar settings, fechar UI e restaurar display
        """
        import time

        engine = self._resolve_tts_engine_package(serial)
        if not engine:
            return (
                "Pós-config TTS: nenhum motor TTS encontrado após a instalação. "
                "Confira se o APK de Síntese de Voz instalou (ex.: com.google.android.tts)."
            )

        self.set_autostart_apps_enabled(serial, False)
        display_state = self._tts_hide_display(serial)
        done: list[str] = [f"motor={engine}"]

        try:
            self._tts_apply_engine_locale(serial, engine)
            done.append("locale=pt-BR")

            # Só fecha Settings; não force-stop do TTS antes do instalador
            self.shell_try(
                serial,
                "am force-stop com.android.tv.settings; "
                "am force-stop com.android.settings; "
                "input keyevent 3",
                timeout_s=5,
            )
            time.sleep(0.08)

            if not self._open_tts_voice_data_install(serial, engine):
                self._tts_apply_engine_locale(serial, engine)
                return (
                    "Pós-config TTS parcial: motor/idioma definidos em silêncio, "
                    "mas o instalador de dados de voz não abriu "
                    f"({engine}/VoiceDataInstallActivity)."
                )
            done.append("instalador-voz")
            time.sleep(0.2)

            # Lista longa: menos dumps, mais DPAD por ciclo
            pt_ok = self._ui_navigate_long_list(
                serial,
                (
                    "Português (Brasil)",
                    "Portugues (Brasil)",
                    "Português - Brasil",
                    "Portuguese (Brazil)",
                    "pt-BR",
                    "Brasil",
                ),
                rounds=10,
                downs_per_round=24,
                pause=0.04,
                dump_every=2,
                hint_substrings=(
                    "brasil",
                    "portug",
                    "portuguese",
                    "pt-br",
                    "idioma",
                    "language",
                ),
                guard_autostart=False,
            )
            if not pt_ok:
                self._tts_apply_engine_locale(serial, engine)
                return (
                    "Pós-config TTS parcial: motor/idioma OK; "
                    "não achou Português (Brasil) no instalador de voz."
                )
            done.append("pt-BR")
            time.sleep(0.12)

            # Dispara download/instalar se o botão estiver visível
            self._ui_navigate_step(
                serial,
                (
                    "Fazer o download do pacote de voz",
                    "Fazer o download",
                    "Fazer download",
                    "Instalar",
                    "Install",
                    "Transferir",
                    "Download",
                    "Baixar",
                    "Atualizar",
                ),
                scrolls=1,
                pause=0.08,
                optional=True,
                guard_autostart=False,
            )

            has_net = self._tts_device_has_network(serial)
            voice_ok = self._tts_wait_and_select_voice_v(
                serial,
                max_rounds=14 if has_net else 4,
            )
            if voice_ok:
                done.append("Voz V")
            elif not has_net:
                done.append("sem-rede")

            self._tts_apply_engine_locale(serial, engine)
            # Acorda o serviço depois da seleção (não antes — force-stop matava o wake)
            self._tts_wake_engine_service(serial, engine)
            verified = self._tts_verify_engine_locale(serial, engine)
            if verified:
                done.append("verificado")

            trail = ", ".join(done)
            if voice_ok and verified:
                return f"Pós-config TTS OK (silenciosa): {trail}."
            if voice_ok:
                return (
                    f"Pós-config TTS OK com ressalva (silenciosa): {trail}. "
                    "Voz V selecionada; confira o motor padrão se necessário."
                )
            if not has_net:
                return (
                    f"Pós-config TTS parcial (silenciosa): {trail}. "
                    "Motor/idioma OK e Português aberto, mas o Mini PC está SEM INTERNET "
                    "(eth0/wlan0 sem link). Conecte cabo de rede ou Wi‑Fi e rode de novo "
                    "para baixar a Voz V (~9 MB)."
                )
            return (
                f"Pós-config TTS parcial (silenciosa): {trail}. "
                "Português encontrado, mas Voz V não foi confirmada "
                "(verifique internet no Mini PC)."
            )
        finally:
            self.close_settings_and_tts_ui(serial)
            self._tts_restore_display(serial, display_state)

    def _tts_apply_engine_locale(self, serial: str, engine: str) -> None:
        """Define motor e locale pt-BR via Settings Secure (sem UI)."""
        self.shell_try(
            serial,
            f"pm enable {engine}; "
            f"settings put secure tts_default_synth {engine}; "
            "settings put secure tts_default_locale pt-BR; "
            "settings put secure tts_default_lang por; "
            "settings put secure tts_default_country BRA",
            timeout_s=8,
        )

    def _tts_verify_engine_locale(self, serial: str, engine: str) -> bool:
        """Confirma que motor/locale ficaram gravados."""
        synth = (self.shell_try(serial, "settings get secure tts_default_synth", timeout_s=4) or "").strip()
        locale = (
            self.shell_try(serial, "settings get secure tts_default_locale", timeout_s=4) or ""
        ).strip()
        return engine in synth and "pt" in locale.lower() and "br" in locale.lower().replace("_", "-")

    def _tts_wake_engine_service(self, serial: str, engine: str) -> None:
        """Inicia o serviço TTS para o motor aplicar locale / baixar voz padrão."""
        self.shell_try(
            serial,
            f"am start-foreground-service -n {engine}/.service.GoogleTTSService "
            "-a android.intent.action.TTS_SERVICE 2>/dev/null || "
            f"am startservice -n {engine}/.service.GoogleTTSService "
            "-a android.intent.action.TTS_SERVICE 2>/dev/null || "
            f"am startservice -a android.intent.action.TTS_SERVICE -p {engine}",
            timeout_s=4,
        )

    def _tts_hide_display(self, serial: str) -> dict[str, str]:
        """Zera brilho/animações para o procedimento não aparecer na tela."""
        state: dict[str, str] = {}
        # Um único shell: lê e aplica (bem mais rápido que N round-trips)
        blob = (
            self.shell_try(
                serial,
                "echo BRIGHT=$(settings get system screen_brightness); "
                "echo BMODE=$(settings get system screen_brightness_mode); "
                "echo WA=$(settings get global window_animation_scale); "
                "echo TA=$(settings get global transition_animation_scale); "
                "echo AA=$(settings get global animator_duration_scale); "
                "settings put system screen_brightness_mode 0; "
                "settings put system screen_brightness 0; "
                "settings put global window_animation_scale 0; "
                "settings put global transition_animation_scale 0; "
                "settings put global animator_duration_scale 0; "
                "svc power stayon true",
                timeout_s=6,
            )
            or ""
        )
        for line in blob.splitlines():
            line = line.strip()
            if line.startswith("BRIGHT="):
                state["system:screen_brightness"] = line.split("=", 1)[-1].strip()
            elif line.startswith("BMODE="):
                state["system:screen_brightness_mode"] = line.split("=", 1)[-1].strip()
            elif line.startswith("WA="):
                state["global:window_animation_scale"] = line.split("=", 1)[-1].strip()
            elif line.startswith("TA="):
                state["global:transition_animation_scale"] = line.split("=", 1)[-1].strip()
            elif line.startswith("AA="):
                state["global:animator_duration_scale"] = line.split("=", 1)[-1].strip()
        return state

    def _tts_restore_display(self, serial: str, state: dict[str, str]) -> None:
        """Restaura brilho/animações salvos antes da config silenciosa."""
        parts = ["svc power stayon false"]
        if not state:
            parts.append("settings put system screen_brightness 128")
        else:
            for compound, val in state.items():
                if ":" not in compound or not val or val.lower() == "null":
                    continue
                ns, key = compound.split(":", 1)
                parts.append(f"settings put {ns} {key} {val}")
        self.shell_try(serial, "; ".join(parts), timeout_s=6)

    def _open_tts_voice_data_install(self, serial: str, engine: str) -> bool:
        """Abre direto o instalador de dados de voz do motor (sem menus Settings)."""
        import time

        cmds = (
            f"am start -a android.speech.tts.engine.INSTALL_TTS_DATA "
            f"-n {engine}/.local.voicepack.ui.VoiceDataInstallActivity",
            f"am start -a android.speech.tts.engine.INSTALL_TTS_DATA -p {engine}",
            f"am start -n {engine}/.local.voicepack.ui.VoiceDataInstallActivity",
            f"am start -a {engine}.settings.EngineSettings "
            f"-n {engine}/.settings.EngineSettings",
        )
        for cmd in cmds:
            try:
                out = self.shell_combined(serial, cmd, timeout_s=8)
            except AdbError:
                continue
            if self._am_start_succeeded(out) or "starting:" in (out or "").lower():
                time.sleep(0.2)
                # Intent iniciou: não espera dump completo (lista pode ainda carregar)
                if "error" not in (out or "").lower() and "exception" not in (out or "").lower():
                    return True
        return False

    def _tts_device_has_network(self, serial: str) -> bool:
        """True se o Mini PC tem rota/default network (necessário p/ baixar voz)."""
        out = (
            self.shell_try(
                serial,
                "dumpsys connectivity 2>/dev/null | grep -i 'Active default network'",
                timeout_s=6,
            )
            or ""
        ).lower()
        if "active default network:" in out and "none" not in out.split(":", 1)[-1]:
            return True
        # Fallback: algum iface com IP além de loopback
        ip = self.shell_try(serial, "ip -o -4 addr show up 2>/dev/null", timeout_s=5) or ""
        for line in ip.splitlines():
            if " lo " in f" {line} " or line.strip().startswith("1:"):
                continue
            if "inet " in line:
                return True
        return False

    def _tts_wait_and_select_voice_v(self, serial: str, *, max_rounds: int = 14) -> bool:
        """Aguarda download (se houver) e seleciona Voz V."""
        import time

        self._ui_tap_label_anywhere(
            serial,
            (
                "Fazer o download do pacote de voz",
                "Fazer o download",
                "Fazer download",
                "Download",
                "Baixar",
            ),
            prefer_left=False,
        )

        for _ in range(max(1, max_rounds)):
            xml = self._dump_ui_xml(serial) or ""
            low = xml.lower()
            if self._ui_activate_labels_in_xml(
                serial, xml, ("Voz V", "voz V", "Voice V")
            ):
                return True
            # "V" exato no mesmo dump (evita segundo uiautomator dump)
            if re.search(r'text="V"', xml) and self._ui_activate_labels_in_xml(
                serial, xml, ("V",), exact_only=True
            ):
                return True
            if "voz v" in low or re.search(r"\bvoice\s*v\b", low):
                time.sleep(0.15)
                continue
            if "fazer o download" in low or "download do pacote" in low:
                self._ui_activate_labels_in_xml(
                    serial,
                    xml,
                    (
                        "Fazer o download do pacote de voz",
                        "Fazer o download",
                        "Fazer download",
                    ),
                )
                time.sleep(0.7)
                continue
            if any(k in low for k in ("baixando", "instalando", "loading", "%", "aguarde")):
                time.sleep(0.7)
                continue
            # Poucos scrolls rápidos atrás da Voz V
            self.shell_try(
                serial,
                "input keyevent 20; input keyevent 20; input keyevent 20; input keyevent 20",
                timeout_s=2,
            )
            time.sleep(0.2)
        return False

    def _resolve_tts_engine_package(self, serial: str) -> str | None:
        """Prefere Google TTS; senão o primeiro pacote *tts* instalado."""
        preferred = "com.google.android.tts"
        path = self.shell_try(serial, f"pm path {preferred}", timeout_s=6) or ""
        if "package:" in path.lower():
            return preferred
        listed = self.shell_try(serial, "pm list packages | grep -i tts", timeout_s=6) or ""
        for line in listed.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            pkg = line.split(":", 1)[-1].strip()
            if pkg:
                return pkg
        return None

    def _ui_navigate_step(
        self,
        serial: str,
        labels: tuple[str, ...],
        *,
        scrolls: int = 2,
        pause: float = 0.05,
        optional: bool = False,
        guard_autostart: bool = False,
    ) -> bool:
        """Seleciona um item na UI; 1 dump por tentativa."""
        import time

        _ = optional
        for attempt in range(max(0, scrolls) + 1):
            if guard_autostart:
                self._ensure_autostart_dead(serial)
            xml = self._dump_ui_xml(serial)
            if self._ui_activate_labels_in_xml(serial, xml, labels):
                time.sleep(pause)
                return True
            if attempt < scrolls:
                self.shell_try(
                    serial,
                    "input keyevent 20; input keyevent 20; input keyevent 20; input keyevent 20",
                    timeout_s=2,
                )
                time.sleep(0.02)
        return False

    def _ui_navigate_long_list(
        self,
        serial: str,
        labels: tuple[str, ...],
        *,
        rounds: int = 18,
        downs_per_round: int = 20,
        pause: float = 0.03,
        dump_every: int = 1,
        hint_substrings: tuple[str, ...] = (),
        guard_autostart: bool = False,
    ) -> bool:
        """Lista longa: muitos DPAD por dump (dump_every>1 reduz dumps)."""
        import time

        downs_cmd = "; ".join(["input keyevent 20"] * max(1, downs_per_round))
        every = max(1, dump_every)
        for i in range(max(1, rounds)):
            if guard_autostart:
                self._ensure_autostart_dead(serial)
            if i % every == 0:
                xml = self._dump_ui_xml(serial)
                low = (xml or "").lower()
                if not (
                    hint_substrings and not any(h in low for h in hint_substrings)
                ):
                    if self._ui_activate_labels_in_xml(serial, xml, labels):
                        time.sleep(pause)
                        return True
            self.shell_try(serial, downs_cmd, timeout_s=2)
            time.sleep(0.02)
        xml = self._dump_ui_xml(serial)
        return self._ui_activate_labels_in_xml(serial, xml, labels)

    def _dump_ui_xml(self, serial: str) -> str:
        """Dump da UI (timeout enxuto para agilizar navegação)."""
        out = self.shell_try(
            serial,
            "uiautomator dump /sdcard/aibox_ui.xml >/dev/null 2>&1; cat /sdcard/aibox_ui.xml",
            timeout_s=5,
        ) or ""
        if "<" in out:
            return out[out.index("<") :]
        return out

    def _ui_select_voice_v(self, serial: str) -> bool:
        """Seleciona item de voz rotulado como V / Voz V."""
        xml = self._dump_ui_xml(serial)
        if self._ui_activate_labels_in_xml(serial, xml, ("Voz V", "voz V", "Voice V")):
            return True
        return self._ui_select_exact_text(serial, "V")

    def _ui_activate_labels_in_xml(
        self,
        serial: str,
        xml: str,
        labels: tuple[str, ...],
        *,
        exact_only: bool = False,
    ) -> bool:
        """Usa um dump já obtido: encontra rótulo e toca (exact + fuzzy numa passada)."""
        import xml.etree.ElementTree as ET

        if not (xml or "").strip().startswith("<"):
            return False
        try:
            root = ET.fromstring(xml[xml.index("<") :] if "<" in xml else xml)
        except ET.ParseError:
            return False

        wanted = tuple(x.strip().lower() for x in labels if x.strip())
        candidates: list[tuple[int, int, int]] = []

        for node in root.iter("node"):
            texts: list[str] = []
            own = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            if own:
                texts.append(own)
            if desc:
                texts.append(desc)
            for c in list(node):
                t = (c.get("text") or "").strip()
                if t:
                    texts.append(t)
            if not texts:
                continue
            score = -1
            for i, lab in enumerate(wanted):
                for t in texts:
                    tl = t.lower().strip()
                    if tl == lab:
                        score = max(score, 1000 - i * 10)
                    elif not exact_only and (lab in tl or tl in lab):
                        score = max(score, 400 - i * 10 - abs(len(tl) - len(lab)))
            if score < 0:
                continue
            center = self._bounds_center(node.get("bounds") or "")
            if center is None:
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (node.get("bounds") or "").strip())
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                w, h = x2 - x1, y2 - y1
                if w > 1600 or h > 360:
                    continue
                if h < 120:
                    score += 15
            x, y = center
            candidates.append((score, x, y))

        if not candidates:
            return False
        candidates.sort(key=lambda t: t[0], reverse=True)
        _, x, y = candidates[0]
        return self._tap_and_confirm(serial, x, y)

    def _ui_select_exact_text(self, serial: str, text: str) -> bool:
        """Toca/foca nó cujo texto é exatamente `text`."""
        import xml.etree.ElementTree as ET

        xml = self._dump_ui_xml(serial)
        if not xml.strip().startswith("<"):
            return False
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False

        target = text.strip()
        for node in root.iter("node"):
            own = (node.get("text") or "").strip()
            if own != target:
                child_hit = False
                for c in node.iter("node"):
                    if (c.get("text") or "").strip() == target:
                        child_hit = True
                        break
                if not child_hit:
                    continue
            center = self._bounds_center(node.get("bounds") or "")
            if center is None:
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (node.get("bounds") or "").strip())
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                if (x2 - x1) > 1200 or (y2 - y1) > 200:
                    continue
            x, y = center
            self._tap_and_confirm(serial, x, y)
            return True
        return False

    def _ui_select_by_labels(
        self,
        serial: str,
        labels: tuple[str, ...],
        *,
        exact_preferred: bool = True,
    ) -> bool:
        """Encontra e ativa o primeiro rótulo visível (tap + CENTER)."""
        _ = exact_preferred  # matching exact+fuzzy já é feito em _ui_activate_labels_in_xml
        xml = self._dump_ui_xml(serial)
        return self._ui_activate_labels_in_xml(serial, xml, labels)

    def uninstall_package(self, serial: str, package_name: str, keep_data: bool = False) -> str:
        package_name = (package_name or "").strip()
        if not package_name:
            raise AdbError("Package name inválido.")
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package_name)
        cp = self._run(args, serial=serial, timeout_s=120)
        msg = str(cp.stdout).strip()
        low = msg.lower()
        if "failure" in low or "failed" in low or "unknown package" in low:
            raise AdbError(msg or f"Falha ao desinstalar {package_name}.")
        return msg or "Success"

    def list_installed_apps(
        self,
        serial: str,
        *,
        include_system: bool = False,
    ) -> list[InstalledApp]:
        """Lista apps instalados com o nome visível (label) quando possível."""
        third = self._list_package_names(serial, third_party_only=True)
        system: set[str] = set()
        if include_system:
            all_pkgs = self._list_package_names(serial, third_party_only=False)
            system = set(all_pkgs) - set(third)
            packages = all_pkgs
        else:
            packages = third

        labels = self._resolve_app_labels(serial, packages)
        apps: list[InstalledApp] = []
        for pkg in packages:
            meta = labels.get(pkg, {})
            label = (meta.get("label") or "").strip() or self._prettify_package_name(pkg)
            apps.append(
                InstalledApp(
                    package=pkg,
                    label=label,
                    version_name=meta.get("version"),
                    system=pkg in system,
                )
            )
        apps.sort(key=lambda a: (a.label.lower(), a.package.lower()))
        return apps

    def _list_package_names(self, serial: str, *, third_party_only: bool) -> list[str]:
        flag = " -3" if third_party_only else ""
        out = self.shell_lax(serial, f"pm list packages{flag}", timeout_s=60)
        pkgs: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            pkg = line.split("package:", 1)[1].strip()
            if pkg:
                pkgs.append(pkg)
        return pkgs

    def _resolve_app_labels(self, serial: str, packages: list[str]) -> dict[str, dict[str, str]]:
        """Resolve label/version via dumpsys (1 dump) + aapt no host quando necessário."""
        result: dict[str, dict[str, str]] = {p: {} for p in packages}
        want = set(packages)
        if not want:
            return result

        dump = self.shell_lax(serial, "dumpsys package", timeout_s=180)
        current: str | None = None
        for line in dump.splitlines():
            m = re.match(r"\s*Package\s+\[([^\]]+)\]", line)
            if m:
                current = m.group(1).strip()
                if current not in want:
                    current = None
                continue
            if current is None:
                continue
            lm = re.search(r"nonLocalizedLabel=(.+)$", line)
            if lm:
                val = lm.group(1).strip()
                if val and val.lower() != "null":
                    result[current]["label"] = val
            vm = re.search(r"versionName=(\S+)", line)
            if vm and "version" not in result[current]:
                result[current]["version"] = vm.group(1).strip()

        missing = [p for p in packages if not result[p].get("label")]
        # aapt no host: só para terceiros sem label (sistema costuma ter só labelRes)
        third_set = set(self._list_package_names(serial, third_party_only=True))
        for pkg in [p for p in missing if p in third_set][:40]:
            label = self._label_via_aapt(serial, pkg)
            if label:
                result[pkg]["label"] = label
            if not result[pkg].get("version"):
                one = self.shell_lax(serial, f"dumpsys package {pkg}", timeout_s=20)
                vm = re.search(r"versionName=(\S+)", one)
                if vm:
                    result[pkg]["version"] = vm.group(1).strip()
                if not result[pkg].get("label"):
                    lm = re.search(r"nonLocalizedLabel=(.+)$", one, re.M)
                    if lm:
                        val = lm.group(1).strip()
                        if val and val.lower() != "null":
                            result[pkg]["label"] = val
        return result

    def _label_via_aapt(self, serial: str, package: str) -> str | None:
        """Tenta ler application-label do APK com aapt no PC (nome real do app)."""
        path_out = self.shell_lax(serial, f"pm path {package}", timeout_s=15)
        apk_path = ""
        for line in path_out.splitlines():
            if line.startswith("package:"):
                apk_path = line.split("package:", 1)[1].strip()
                if apk_path.endswith(".apk"):
                    break
        if not apk_path:
            return None

        aapt = self._find_aapt()
        if aapt is None:
            return None

        import tempfile

        with tempfile.TemporaryDirectory(prefix="aibox_apk_") as tmp:
            local = Path(tmp) / "base.apk"
            try:
                self._run(["pull", apk_path, str(local)], serial=serial, timeout_s=90)
            except AdbError:
                return None
            if not local.exists() or local.stat().st_size < 64:
                return None
            try:
                cp = run_hidden(
                    [aapt, "dump", "badging", str(local)],
                    capture_output=True,
                    timeout=40,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            text = (cp.stdout or b"").decode("utf-8", "replace")
            # application-label:'Nome'  (locale default)
            m = re.search(r"application-label(?:-pt|-pt-BR|-en)?:'([^']+)'", text)
            if not m:
                m = re.search(r"application-label:'([^']+)'", text)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _find_aapt() -> str | None:
        which = shutil.which("aapt") or shutil.which("aapt.exe")
        if which:
            return which
        local_app = Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "build-tools"
        if local_app.exists():
            cands = sorted(local_app.glob("*/aapt.exe"), reverse=True)
            if cands:
                return str(cands[0])
        return None

    @staticmethod
    def _prettify_package_name(package: str) -> str:
        tail = package.rsplit(".", 1)[-1]
        parts = re.split(r"[_\-.]+", tail)
        return " ".join(p.capitalize() for p in parts if p) or package

    @staticmethod
    def _parse_mem_kb(meminfo: str, key: str) -> int | None:
        m = re.search(rf"^{re.escape(key)}:\s+(\d+)\s+kB", meminfo or "", re.M)
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_df_used_kb(df_out: str) -> int | None:
        """Usa a 1ª linha de dados do `df -k` (ex.: /data)."""
        for line in (df_out or "").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith("/"):
                try:
                    return int(parts[2])  # Used
                except ValueError:
                    continue
        return None

    @staticmethod
    def _parse_du_kb(du_out: str) -> int:
        total = 0
        for line in (du_out or "").splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                total += int(parts[0])
            except ValueError:
                continue
        return total

    @staticmethod
    def _fmt_kb(kb: int | None) -> str:
        if kb is None:
            return "—"
        if kb >= 1024 * 1024:
            return f"{kb / (1024 * 1024):.2f} GB"
        if kb >= 1024:
            return f"{kb / 1024:.1f} MB"
        return f"{kb} KB"

    def _optimize_cache_footprint_kb(self, serial: str) -> tuple[int, str]:
        """Soma aproximada de caches acessíveis sem root (para medir efeito real)."""
        cmd = (
            "du -sk "
            "/data/local/tmp "
            "/sdcard/LOST.DIR "
            "/sdcard/.trash "
            "/sdcard/Trash "
            "/sdcard/Android/data/*/cache "
            "/sdcard/Android/data/*/code_cache "
            "/sdcard/Android/obb/.cache "
            "2>/dev/null"
        )
        out = self.shell_lax(serial, cmd, timeout_s=60)
        return self._parse_du_kb(out), out

    def _optimize_dexopt(self, serial: str) -> tuple[bool, str]:
        """Tenta dexopt com fallbacks — alguns firmwares quebram em bg-dexopt-job (NPE)."""
        attempts = [
            ("cmd package bg-dexopt-job", 120),
            ("pm bg-dexopt-job", 120),
            # Compila perfil de velocidade (mais lento, mas costuma funcionar sem o job quebrado)
            ("cmd package compile -m speed-profile -a", 300),
        ]
        notes: list[str] = []
        for cmd, timeout in attempts:
            out = self.shell_lax(serial, cmd, timeout_s=timeout)
            text = (out or "").strip()
            low = text.lower()
            failed = any(
                s in low
                for s in (
                    "exception",
                    "nullpointer",
                    "error:",
                    "failed",
                    "unknown command",
                    "inaccessible or not found",
                )
            )
            if not failed:
                label = "ok (sem saída)" if not text else text[:240]
                notes.append(f"OK via `{cmd}`: {label}")
                return True, "\n".join(notes)
            notes.append(f"Falhou `{cmd}`: {(text[:180] or 'sem detalhes')}")
        notes.append(
            "Dexopt não suportado neste firmware (bug comum em alguns Android TV/custom). "
            "Os demais passos de limpeza/RAM seguem válidos."
        )
        return False, "\n".join(notes)

    def optimize_device(self, serial: str) -> str:
        """Otimização completa (cache, armazenamento, RAM, diagnóstico) sem apagar dados do usuário."""
        import time

        lines: list[str] = ["=== Diagnóstico inicial ==="]
        df_before = self.shell_lax(serial, "df -k /data 2>/dev/null | tail -n +2", timeout_s=15)
        storage_before_h = self.shell_lax(
            serial,
            "df -h /data /sdcard /storage/emulated/0 2>/dev/null | head -n 20",
            timeout_s=20,
        )
        mem_before = self.shell_lax(
            serial,
            "cat /proc/meminfo | grep -E 'MemTotal|MemFree|MemAvailable|Cached|Buffers'",
            timeout_s=10,
        )
        used_before = self._parse_df_used_kb(df_before)
        avail_before = self._parse_mem_kb(mem_before, "MemAvailable")
        free_before = self._parse_mem_kb(mem_before, "MemFree")
        cache_before, _ = self._optimize_cache_footprint_kb(serial)

        lines.append(storage_before_h or "(sem df)")
        lines.append(mem_before or "(sem meminfo)")
        lines.append(
            f"Medição: disco usado={self._fmt_kb(used_before)} · "
            f"caches acessíveis≈{self._fmt_kb(cache_before)} · "
            f"RAM disponível={self._fmt_kb(avail_before)}"
        )

        results: dict[str, str] = {}

        # 1) Limpar caches de apps (seguro — não apaga dados)
        lines.append("\n=== Limpeza de cache de apps ===")
        trim = self.shell_lax(serial, "pm trim-caches 512G", timeout_s=120)
        trim_txt = (trim or "").strip()
        trim_low = trim_txt.lower()
        if any(s in trim_low for s in ("exception", "error", "unknown command", "denied")):
            lines.append(f"AVISO: {trim_txt[:300]}")
            results["trim"] = "falhou"
        else:
            lines.append(trim_txt or "pm trim-caches executado (saída vazia = sucesso típico).")
            results["trim"] = "ok"

        # 2) Arquivos temporários / lixo comum (sem tocar em fotos/docs do usuário)
        lines.append("\n=== Limpeza de temporários ===")
        cleanup_targets = [
            ("/data/local/tmp", "tmp local"),
            ("/sdcard/LOST.DIR", "LOST.DIR"),
            ("/sdcard/.trash", "lixeira oculta"),
            ("/sdcard/Trash", "Trash"),
            ("/sdcard/Android/data/*/cache", "cache apps (sdcard)"),
            ("/sdcard/Android/data/*/code_cache", "code_cache (sdcard)"),
            ("/sdcard/Android/obb/.cache", "obb cache"),
        ]
        for path, label in cleanup_targets:
            before = self._parse_du_kb(
                self.shell_lax(serial, f"du -sk {path} 2>/dev/null", timeout_s=30)
            )
            self.shell_lax(serial, f"rm -rf {path}/* {path}/.[!.]* {path}/..?* 2>/dev/null", timeout_s=45)
            after = self._parse_du_kb(
                self.shell_lax(serial, f"du -sk {path} 2>/dev/null", timeout_s=30)
            )
            freed = max(0, before - after)
            if before <= 0 and after <= 0:
                lines.append(f"• {label}: vazio / sem acesso")
            elif freed > 0:
                lines.append(f"• {label}: liberou {self._fmt_kb(freed)}")
            else:
                lines.append(f"• {label}: sem alteração ({self._fmt_kb(before)})")
        results["temp"] = "ok"

        # 3) Liberar RAM — encerra processos em background
        lines.append("\n=== Otimização de memória RAM ===")
        kill = self.shell_lax(serial, "am kill-all", timeout_s=30)
        kill_txt = (kill or "").strip()
        if kill_txt and any(s in kill_txt.lower() for s in ("exception", "error", "unknown")):
            lines.append(f"AVISO am kill-all: {kill_txt[:240]}")
            results["ram"] = "parcial"
        else:
            lines.append(kill_txt or "am kill-all enviado (encerra apps em 2º plano).")
            results["ram"] = "ok"
        # drop_caches exige root
        drop = self.shell_lax(
            serial,
            "su 0 sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || "
            "su -c 'sync; echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || echo drop_caches_sem_root",
            timeout_s=15,
        )
        drop_txt = (drop or "").strip()
        if "drop_caches_sem_root" in drop_txt or "Permission denied" in drop_txt:
            lines.append("drop_caches: pulado (requer root — normal sem su).")
        else:
            lines.append(f"drop_caches: {drop_txt[:200] or 'ok'}")
        time.sleep(0.6)

        # 4) Compilação/otimização de bytecode
        lines.append("\n=== Otimização de processamento (dexopt) ===")
        dex_ok, dex_msg = self._optimize_dexopt(serial)
        lines.append(dex_msg)
        results["dexopt"] = "ok" if dex_ok else "falhou"

        # 5) Busca de erros recentes (ANR / FATAL)
        lines.append("\n=== Análise de erros recentes ===")
        crash = self.shell_lax(
            serial,
            "logcat -d -t 400 *:E 2>/dev/null | grep -iE 'FATAL EXCEPTION|ANR in|OutOfMemory|am_crash|lowmemory' | tail -n 40",
            timeout_s=40,
        )
        if crash.strip():
            lines.append(crash.strip())
            pkgs = re.findall(r"([a-zA-Z0-9_]+\.[a-zA-Z0-9_\.]+)", crash)
            skip = {"java.lang", "android.os", "android.app", "com.android.server"}
            counts: dict[str, int] = {}
            for p in pkgs:
                if p.startswith("com.android.") and p.count(".") < 3:
                    continue
                if any(p.startswith(s) for s in skip):
                    continue
                if p.count(".") >= 1:
                    counts[p] = counts.get(p, 0) + 1
            top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
            if top:
                lines.append("Possíveis apps relacionados a erros:")
                for p, n in top:
                    lines.append(f"  • {p} ({n})")
            results["log"] = "achados"
        else:
            lines.append("Nenhum FATAL/ANR recente encontrado no buffer de log.")
            results["log"] = "limpo"

        self.shell_lax(serial, "logcat -c", timeout_s=10)
        lines.append("Buffer de logcat limpo.")

        # 6) Snapshot final + deltas reais
        lines.append("\n=== Resultado ===")
        df_after = self.shell_lax(serial, "df -k /data 2>/dev/null | tail -n +2", timeout_s=15)
        storage_after_h = self.shell_lax(
            serial,
            "df -h /data /sdcard /storage/emulated/0 2>/dev/null | head -n 20",
            timeout_s=20,
        )
        mem_after = self.shell_lax(
            serial,
            "cat /proc/meminfo | grep -E 'MemTotal|MemFree|MemAvailable|Cached|Buffers'",
            timeout_s=10,
        )
        used_after = self._parse_df_used_kb(df_after)
        avail_after = self._parse_mem_kb(mem_after, "MemAvailable")
        free_after = self._parse_mem_kb(mem_after, "MemFree")
        cache_after, _ = self._optimize_cache_footprint_kb(serial)

        disk_delta = None if used_before is None or used_after is None else used_before - used_after
        cache_delta = cache_before - cache_after
        ram_delta = None if avail_before is None or avail_after is None else avail_after - avail_before
        free_delta = None if free_before is None or free_after is None else free_after - free_before

        lines.append("Armazenamento após:")
        lines.append(storage_after_h or "(sem df)")
        lines.append("Memória após:")
        lines.append(mem_after or "(sem meminfo)")
        lines.append("")
        lines.append("Variação medida:")
        if disk_delta is not None:
            if disk_delta > 64:
                lines.append(f"• Disco (/data): liberou ≈ {self._fmt_kb(disk_delta)}")
            elif disk_delta < -64:
                lines.append(f"• Disco (/data): aumentou ≈ {self._fmt_kb(-disk_delta)} (uso normal do sistema)")
            else:
                lines.append(
                    f"• Disco (/data): sem mudança relevante "
                    f"({self._fmt_kb(used_after)} usados — aparelho já estava limpo ou caches internos inacessíveis sem root)"
                )
        if cache_delta > 0:
            lines.append(f"• Caches acessíveis: liberou ≈ {self._fmt_kb(cache_delta)}")
        else:
            lines.append(
                f"• Caches acessíveis: sem mudança relevante (antes {self._fmt_kb(cache_before)})"
            )
        if ram_delta is not None:
            if ram_delta > 1024:
                lines.append(f"• RAM disponível: +{self._fmt_kb(ram_delta)}")
            elif ram_delta < -1024:
                lines.append(f"• RAM disponível: {self._fmt_kb(ram_delta)}")
            else:
                extra = ""
                if free_delta is not None and abs(free_delta) > 1024:
                    sign = "+" if free_delta > 0 else ""
                    extra = f" · MemFree {sign}{self._fmt_kb(abs(free_delta))}"
                lines.append(f"• RAM disponível: estável ({self._fmt_kb(avail_after)}{extra})")

        lines.append("")
        lines.append("Status por etapa:")
        lines.append(f"• trim-caches: {results.get('trim', '?')}")
        lines.append(f"• temporários: {results.get('temp', '?')}")
        lines.append(f"• RAM (kill-all): {results.get('ram', '?')}")
        lines.append(f"• dexopt: {results.get('dexopt', '?')}")
        lines.append(f"• análise de log: {results.get('log', '?')}")
        lines.append(
            "\nDados do usuário (fotos, docs, apps) foram preservados. "
            "Sem root, caches internos de /data/data podem não mudar no df — "
            "mesmo assim o trim-caches e o kill-all atuam no sistema."
        )
        return "\n".join(lines)

    def factory_reset(self, serial: str) -> tuple[str, bool]:
        """Inicia restauração de fábrica.

        Retorna (mensagem, deve_desconectar).

        Importante: «Broadcast completed: result=0» NÃO prova que o wipe
        começou — em muitos aparelhos o intent é engolido sem efeito.
        Só consideramos sucesso se o dispositivo cair offline/rebootar.
        """
        # 1) UI primeiro (caminho que funciona nestes Mini PCs/TV — bem mais rápido).
        try:
            opened = self._open_factory_reset_ui(serial)
            on_reset = False
            try:
                on_reset = self._is_factory_reset_screen(serial)
            except AdbError as e:
                if self._looks_like_device_gone(str(e).lower()):
                    return (
                        "Restauração iniciada. A conexão caiu durante o reset (comportamento esperado).",
                        True,
                    )
                on_reset = False
            if opened or on_reset:
                try:
                    confirmed = self._confirm_factory_reset_on_ui(serial)
                except AdbError as e:
                    if self._looks_like_device_gone(str(e).lower()):
                        return (
                            "Restauração iniciada. A conexão caiu durante o reset (comportamento esperado).",
                            True,
                        )
                    raise
                if confirmed:
                    return (
                        "Confirmação enviada no aparelho.\n"
                        "O dispositivo deve apagar os dados e reiniciar (pode demorar alguns minutos).",
                        True,
                    )
                return (
                    "Tela de redefinição aberta e tentativa de confirmação enviada.\n"
                    "Se o aparelho não reiniciar, confirme «Redefinir» / «Apagar tudo» na tela.",
                    False,
                )
        except AdbError as e:
            if self._looks_like_device_gone(str(e).lower()):
                return (
                    "Restauração iniciada. A conexão caiu durante o reset (comportamento esperado).",
                    True,
                )

        # 2) Wipe direto (rápido; espera curta — em userdebug sem su costuma não ter efeito).
        wipe_commands = [
            "am broadcast -a android.intent.action.FACTORY_RESET --receiver-include-background -f 0x01000000",
            "am broadcast -a android.intent.action.MASTER_CLEAR --receiver-include-background -f 0x01000000",
            "su 0 am broadcast -a android.intent.action.MASTER_CLEAR --user 0",
            "su 0 cmd wipe data",
        ]

        last_err = ""
        for cmd in wipe_commands:
            try:
                out = self.shell_combined(serial, cmd, timeout_s=20).strip()
            except AdbError as e:
                msg = str(e)
                low = msg.lower()
                if self._looks_like_device_gone(low) and "permission" not in low:
                    return (
                        "Restauração iniciada. O dispositivo desconectou durante o processo (comportamento esperado).",
                        True,
                    )
                last_err = msg
                continue

            if self._factory_reset_output_failed(out):
                last_err = out or last_err
                continue

            if self._wait_device_offline(serial, timeout_s=2.0):
                detail = out.strip() if out.strip() else cmd
                return (
                    f"Restauração iniciada (dispositivo offline após o comando).\n{detail}",
                    True,
                )
            last_err = (
                (out.strip() + "\n") if out.strip() else ""
            ) + "Comando aceito pelo ADB, mas o aparelho continuou online (wipe não iniciado)."

        # Último recurso: abre Configurações gerais + instrução manual clara.
        settings_opened = False
        for cmd in (
            "am start -a android.settings.SETTINGS",
            "am start -n com.android.settings/.Settings",
        ):
            try:
                out = self.shell_combined(serial, cmd, timeout_s=15)
            except AdbError as e:
                last_err = str(e)
                continue
            if self._am_start_succeeded(out):
                settings_opened = True
                break

        manual = (
            "Não foi possível iniciar nem abrir a tela de restauração automaticamente.\n\n"
            "Faça no aparelho:\n"
            "1) Configurações → Sistema (ou Sobre / Privacidade)\n"
            "2) Redefinir opções / Restauração de fábrica / Apagar todos os dados\n"
            "3) Confirme na tela do dispositivo\n\n"
            "Em Mini PCs/totens o caminho pode variar conforme o fabricante."
        )
        if settings_opened:
            return (
                "Configurações foi aberta no aparelho.\n\n" + manual,
                False,
            )
        raise AdbError((last_err + "\n\n" if last_err else "") + manual)

    def _open_factory_reset_ui(self, serial: str) -> bool:
        """Tenta abrir a UI real de factory reset; True só com confirmação na tela."""
        import time

        # Já está na tela de reset (ex.: tentativa anterior)
        try:
            if self._is_factory_reset_screen(serial, allow_ui_dump=False):
                return True
        except AdbError:
            pass

        # Ordem: PrivacyActivity = «Redefinir» (wipe). Sem probes lentos no caminho feliz.
        ui_commands = [
            "am start -n com.android.tv.settings/.device.privacy.PrivacyActivity",
            "am start -a android.settings.FACTORY_RESET",
            "am start -n com.android.settings/.MasterClearConfirm",
            "am start -n com.android.settings/.MasterClear",
        ]

        for cmd in ui_commands:
            if "storagereset" in cmd.lower():
                continue
            try:
                out = self.shell_combined(serial, cmd, timeout_s=15)
            except AdbError:
                continue
            if not self._am_start_succeeded(out):
                if self._is_factory_reset_screen(serial, allow_ui_dump=False):
                    return True
                continue
            time.sleep(0.35)
            if self._is_factory_reset_screen(serial, allow_ui_dump=False):
                return True
            time.sleep(0.25)
            if self._is_factory_reset_screen(serial, allow_ui_dump=True):
                return True
        return self._is_factory_reset_screen(serial, allow_ui_dump=True)

    def _is_factory_reset_screen(self, serial: str, *, allow_ui_dump: bool = True) -> bool:
        """True se a UI em foco for de redefinição de fábrica (não a lista de armazenamento)."""
        focus = self.shell_lax(
            serial,
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
            timeout_s=6,
        ).lower()

        # StorageResetActivity = lista «Armazenamento» (NÃO é wipe)
        if "storageresetactivity" in focus.replace("/", "."):
            return False

        # Neste firmware, PrivacyActivity abre device.storage.ResetActivity («Redefinir»)
        if any(
            t in focus
            for t in (
                "privacyactivity",
                "storage.resetactivity",
                ".device.storage.resetactivity",
                "factoryreset",
                "masterclear",
                "masterclearconfirm",
            )
        ):
            return True

        if not allow_ui_dump:
            return False
        return self._ui_text_looks_like_factory_reset(serial)

    def _ui_text_looks_like_factory_reset(self, serial: str) -> bool:
        xml = self._dump_ui_xml(serial).lower()
        if "armazenamento" in xml and "redefinir" not in xml and "apag" not in xml:
            return False
        has_reset_word = any(
            t in xml
            for t in (
                "redefinir",
                "factory reset",
                "master clear",
                "restauração de fábrica",
                "restauracao de fabrica",
                "apagar tudo",
            )
        )
        has_wipe_hint = any(
            t in xml
            for t in ("apag", "dados do armazenamento", "conta do google", "wipe", "todos os dados", "cancelar")
        )
        return has_reset_word and has_wipe_hint

    def _confirm_factory_reset_on_ui(self, serial: str) -> bool:
        """Confirma wipe via DPAD em 1–2 telas Leanback (Redefinir → Apagar tudo).

        Caminho rápido: no máximo 1 dump por tela; DOWN+CENTER sem revalidar com dump.
        Nestes Mini PCs «Cancelar» inicia focado — um DOWN chega em «Redefinir»/«Apagar tudo».
        """
        import time

        for _ in range(2):
            if self._wait_device_offline(serial, timeout_s=0.2):
                return True

            label = self._focused_action_label(serial)
            if self._is_wipe_confirm_label(label):
                # Já no botão certo → só confirma
                self.shell_lax(serial, "input keyevent 23", timeout_s=3)
            else:
                # Cancelar (padrão) → desce e confirma no mesmo shell (sem dump entre eles)
                self.shell_lax(
                    serial,
                    "input keyevent 20; sleep 0.2; input keyevent 23",
                    timeout_s=4,
                )

            # Próxima tela aparece rápido; wipe pode derrubar o ADB
            if self._wait_device_offline(serial, timeout_s=2.5):
                return True
            time.sleep(0.35)

        return self._wait_device_offline(serial, timeout_s=20.0)

    def _leanback_focus_and_confirm(self, serial: str) -> bool:
        """Compat: uma confirmação Leanback rápida."""
        label = self._focused_action_label(serial)
        if self._is_wipe_confirm_label(label):
            self.shell_lax(serial, "input keyevent 23", timeout_s=3)
            return True
        self.shell_lax(serial, "input keyevent 20; sleep 0.2; input keyevent 23", timeout_s=4)
        return True

    @staticmethod
    def _is_cancel_label(label: str | None) -> bool:
        low = (label or "").strip().lower()
        if not low:
            return False
        return low in {"cancelar", "cancel", "voltar"} or low.startswith("cancel")

    @staticmethod
    def _is_wipe_confirm_label(label: str | None) -> bool:
        low = (label or "").strip().lower()
        if not low or Adb._is_cancel_label(low):
            return False
        targets = (
            "apagar tudo",
            "redefinir",
            "erase everything",
            "factory reset",
            "erase all",
            "delete all",
        )
        return any(low == t or t in low for t in targets)

    def _ui_has_wipe_confirm_actions(self, serial: str) -> bool:
        """True se a UI atual mostra ações de confirmação de wipe (2ª tela inclusive)."""
        xml = self._dump_ui_xml(serial).lower()
        has_confirm = any(
            t in xml
            for t in ("apagar tudo", "redefinir", "erase everything", "factory reset")
        )
        has_cancel = "cancelar" in xml or ">cancel<" in xml
        return has_confirm and has_cancel

    def _focused_action_label(self, serial: str) -> str | None:
        """Texto da ação Leanback atualmente focada (ex.: Cancelar / Redefinir / Apagar tudo)."""
        import xml.etree.ElementTree as ET

        xml = self._dump_ui_xml(serial)
        if not xml.strip().startswith("<"):
            return None
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None

        for node in root.iter("node"):
            if node.get("focused") != "true":
                continue
            for c in node.iter("node"):
                rid = (c.get("resource-id") or "").lower()
                t = (c.get("text") or "").strip()
                if t and "guidedactions_item_title" in rid:
                    return t
            own = (node.get("text") or "").strip()
            if own:
                return own
            for c in list(node):
                t = (c.get("text") or "").strip()
                if t:
                    return t
                for gc in list(c):
                    gt = (gc.get("text") or "").strip()
                    if gt:
                        return gt
        return None

    def _tap_ui_action(
        self,
        serial: str,
        *,
        confirm_labels: tuple[str, ...],
        cancel_labels: tuple[str, ...],
    ) -> bool:
        """Toca o botão de confirmação (não o título nem Cancelar)."""
        import xml.etree.ElementTree as ET

        xml = self._dump_ui_xml(serial)
        if "<hierarchy" not in xml and "<node" not in xml:
            xml = self.shell_lax(serial, "uiautomator dump /dev/tty", timeout_s=8)
            if "<" in xml:
                xml = xml[xml.index("<") :]
        if not xml.strip().startswith("<"):
            return False
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return False

        confirm_l = tuple(x.lower() for x in confirm_labels)
        cancel_l = tuple(x.lower() for x in cancel_labels)
        candidates: list[tuple[int, int, int, int]] = []

        for node in root.iter("node"):
            if node.get("clickable") != "true":
                continue
            own = (node.get("text") or "").strip()
            child_texts = [
                (c.get("text") or "").strip()
                for c in list(node)
                if (c.get("text") or "").strip()
            ]
            texts = [t for t in ([own] if own else []) + child_texts if t]
            if not texts:
                texts = [
                    (c.get("text") or "").strip()
                    for c in node.iter("node")
                    if (c.get("text") or "").strip()
                ]
            if not texts:
                continue
            blob = " ".join(texts).lower()
            if any(c in blob for c in cancel_l):
                continue
            if not any(label == t.lower() or label in t.lower() for t in texts for label in confirm_l):
                continue
            center = self._bounds_center(node.get("bounds") or "")
            if center is None:
                continue
            x, y = center
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", (node.get("bounds") or "").strip())
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            w, h = x2 - x1, y2 - y1
            if w > 1600 or h > 400:
                continue
            rid = (node.get("resource-id") or "").lower()
            exact = any(t.lower() in confirm_l for t in texts)
            score = 0
            if exact:
                score += 50
            if "guided" in rid or "action" in rid or "button" in rid:
                score += 30
            score += min(y // 10, 80)
            if h < 120:
                score += 10
            candidates.append((score, y, x, 1))

        if not candidates:
            return False
        candidates.sort(key=lambda t: t[0], reverse=True)
        _, y, x, _ = candidates[0]
        self.shell_lax(serial, f"input tap {x} {y}", timeout_s=3)
        return True

    @staticmethod
    def _bounds_center(bounds: str) -> tuple[int, int] | None:
        # bounds=[x1,y1][x2,y2]
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds.strip())
        if not m:
            return None
        x1, y1, x2, y2 = map(int, m.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2

    def _try_open_reset_from_about(self, serial: str) -> bool:
        return False

    def _discover_factory_reset_commands(self, serial: str) -> list[str]:
        commands: list[str] = [
            "am start -n com.android.tv.settings/.device.privacy.PrivacyActivity",
        ]
        probes = [
            "cmd package resolve-activity --brief -a android.settings.FACTORY_RESET",
            "cmd package resolve-activity --brief -a android.intent.action.FACTORY_RESET",
            "dumpsys package com.android.tv.settings | grep -i 'PrivacyActivity\\|FactoryReset\\|MasterClear'",
        ]
        for probe in probes:
            try:
                out = self.shell_combined(serial, probe, timeout_s=15)
            except AdbError:
                continue
            for line in out.splitlines():
                m = re.search(r"(com\.[\w.]+/\.[\w.]+)", line)
                if not m:
                    continue
                component = m.group(1)
                low = component.lower()
                if "storagereset" in low:
                    continue
                if any(k in low for k in ("privacy", "factory", "masterclear", "wipe")):
                    commands.append(f"am start -n {component}")
        return commands

    def _factory_reset_ui_is_foreground(self, serial: str) -> bool:
        return self._is_factory_reset_screen(serial)

    @staticmethod
    def _am_start_succeeded(out: str) -> bool:
        low = (out or "").lower()
        # Já está no topo: am start avisa "Activity not started, intent has been delivered..."
        if "intent has been delivered to currently running" in low:
            return True
        # `am start` imprime "Starting:" mesmo quando falha em seguida.
        fatal = (
            "unable to resolve",
            "does not exist",
            "error type",
            "permission denial",
            "no activity found",
            "unable to find",
            "unable to start",
            "activity class",
            "exception",
        )
        if any(t in low for t in fatal):
            return False
        # "Activity not started" sem "currently running" = falha real
        if "activity not started" in low:
            return False
        return ("starting:" in low) or ("start success" in low) or ("status: ok" in low)

    def _wait_device_offline(self, serial: str, *, timeout_s: float = 18.0) -> bool:
        """True se o device sumir/ficar offline após o comando de wipe."""
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                devices = self.list_devices()
            except AdbError:
                return True
            match = next((d for d in devices if d.serial == serial), None)
            if match is None:
                return True
            if match.state != "device":
                return True
            # Poll rápido — evita shell ping a cada ciclo (muito lento no Wi‑Fi)
            time.sleep(0.35)
        return False

    @staticmethod
    def _looks_like_device_gone(low: str) -> bool:
        return any(
            token in low
            for token in (
                "device offline",
                "device not found",
                "error: closed",
                "error: connection",
                "connection reset",
                "connection refused",
                "cannot connect",
                "no devices/emulators",
                "not found",
                "offline",
                "disconnected",
                "protocol fault",
                "remote end closed",
                "timeout executando",
            )
        )

    @staticmethod
    def _factory_reset_output_failed(out: str) -> bool:
        low = (out or "").lower()
        return any(
            token in low
            for token in (
                "permission denial",
                "securityexception",
                "not allowed",
                "requires null or",
                "requires android.permission",
                "java.lang.",
                "exception:",
                "does not exist",
                "unable to find",
                "not exported",
                "unknown command",
                "inaccessible or not found",
            )
        )

    def shell(self, serial: str, cmd: str, timeout_s: int | None = 60) -> str:
        cp = self._run(["shell", cmd], serial=serial, timeout_s=timeout_s)
        return str(cp.stdout)

    def shell_combined(self, serial: str, cmd: str, timeout_s: int | None = 60) -> str:
        """stdout+stderr — necessário porque `am start` imprime em stderr."""
        cp = self._run(["shell", cmd], serial=serial, timeout_s=timeout_s)
        return f"{cp.stdout or ''}{cp.stderr or ''}".strip()

    def shell_lax(self, serial: str, cmd: str, timeout_s: int | None = 60) -> str:
        """Como shell_combined, mas não falha se exit code != 0 (input/grep/uiautomator)."""
        full = [self.adb_path, "-s", serial, "shell", cmd]
        try:
            cp = run_hidden(
                full,
                capture_output=True,
                timeout=timeout_s,
                cwd=self._adb_dir,
                env=self._adb_env(),
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as e:
            raise AdbError(f"ADB inválido: {self.adb_path}") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"Timeout executando: {shlex.join(full)}") from e
        out = (cp.stdout or b"").decode("utf-8", "replace")
        err = (cp.stderr or b"").decode("utf-8", "replace")
        return f"{out}{err}".strip()


    def start_logcat(self, serial: str, extra_args: list[str] | None = None) -> subprocess.Popen:
        cmd = [self.adb_path, "-s", serial, "logcat"]
        if extra_args:
            cmd += extra_args
        return popen_hidden(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self._adb_dir,
            env=self._adb_env(),
        )
