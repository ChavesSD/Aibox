# Aibox — guia para o Claude (poupar tokens)

App desktop Windows (PySide6) da Intelite para configurar totens/painéis Android via ADB (USB/Wi‑Fi), instalar APKs, DPI, gravar tela/áudio, otimizar, reset de fábrica. Repo: `ChavesSD/Aibox`; releases/APKs/catálogo: `ChavesSD/ReleasesAibox`. Idioma da UI e dos commits: **português (pt-BR)**.

## Comandos
- Rodar: `python run_aibox.py` · deps: `pip install -r aibox/requirements.txt` (PySide6, qtawesome, pyinstaller)
- Testes: `python -m pytest -q` (~30s, 74 testes, sem dispositivo real)
- Instalador: `python build_exe.py` → `dist\release\Aibox-Setup.exe` (APKs NÃO embarcados; ADB vem de `aibox/platform-tools`)
- APKs: `python publish_apks.py --version X --upload`

## Mapa (pedido → arquivo)
| Tema | Arquivo |
|---|---|
| Toda a UI e fluxos (abas, wifi, gravação, instalação, reset, update) | `aibox/main_window.py` (~3300 linhas; abas em `_tab_*`) |
| Chamadas ADB, TTS "Síntese de Voz" por UI-dump, autostart, optimize, factory reset | `aibox/adb.py` (~4200 linhas; classe `Adb`) |
| Tema/QSS, cores, **`APP_VERSION`**, URLs dos manifestos | `aibox/theme.py` |
| Atualização do app (semver, manifesto, download, sha256, helper) | `aibox/updater.py`, `aibox/update_helper.py` |
| Catálogo/sync de APKs | `aibox/apks_catalog.py`, `aibox/apk_sync.py`, `aibox/github_fetch.py` |
| Gravação de tela (ffmpeg/ffprobe, mux) | `aibox/recording.py` |
| Áudio do dispositivo (scrcpy/ALSA) | `aibox/device_audio.py` |
| Threads | `aibox/workers.py` (`Worker`/`Background`) |
| USB Windows | `aibox/usbwin.py` · caminhos: `aibox/paths.py` |
| Instalador | `installer/*` (Inno `Aibox.iss`, `setup_main.py`) |
| Notas de versão | `release_notes/<versão>.json` |

Estrutura do `main_window.py`: helpers de layout (`_make_page/_make_card/...`) → `_tab_*` constroem páginas → handlers (`connect_wifi`, `install_selected_apks`, `start_factory_reset`...) usam `Background` para não travar a UI.

## Convenções
- Releases: bump em `theme.APP_VERSION` + novo `release_notes/X.Y.Z.json` (`added/fixed/removed`) + commit "Descrição (X.Y.Z)." — padrão do histórico.
- Fluxos de UI Android (TTS, autostart, reset) dependem de `uiautomator dump` e labels em pt/en; use os helpers `_ui_*`/`_tts_*`/`xml_*` existentes (testáveis com XML em `aibox/tests/`), não reescreva a lógica.
- Subprocessos no Windows: usar `procutil.py` (evita janela de console, ex.: find.exe 1.0.8).
- Ícone: usar `Aibox.ico` nativo (`paths.app_icon_path`).
- Não ler `adb.py`/`main_window.py` inteiros: use Grep pelo nome da função e leia só o trecho.

## Estado do working tree (07/10/2026)
Alterações não commitadas: `device_audio.py` + `test_device_audio.py` (novos), mudanças em `main_window.py`, `recording.py`, `Aibox.spec`, `build_exe.py`, `.gitignore` — trabalho em andamento de **captura de áudio do dispositivo (scrcpy)**. Não descartar.

## Segurança/robustez já implementada (Lotes A e B, 07/10/2026)
- `adb.py`: reset de fábrica só reporta sucesso se o aparelho realmente cair; ENTER por DPAD só com foco validado; sem retry de comandos não idempotentes; `restart_guard` impede `kill-server` durante operações; pacotes validados (`is_valid_package_name`) e protegidos contra desinstalação (`uninstall_protection`).
- `main_window.py`: `_device_busy()`, `_active_operations()`, `_shutdown_work()` (closeEvent confirma e encerra threads), `_retire_thread()` (sem wait na UI), `_quick_action()`, `_guarded()` nas filas.
- Threads de gravação: `stop()` só sinaliza; a própria thread encerra os processos.
- Testes: `test_safety_lote_a.py`, `test_safety_lote_b.py`. 

## Lote C — downloads/atualização/instalador (07/10/2026)
- `github_fetch.py`: `safe_urlopen`/`check_url` (só HTTPS + allowlist de hosts, inclusive em redirects), `read_limited`; manifestos nunca vêm do jsDelivr.
- `updater.py`: manifesto exige `sha256` (64 hex), nome `*.zip` simples e URL permitida; `download_asset(expected_sha256=…)` valida no `.part` antes de trocar o destino; limites de tamanho/tempo; env `AIBOX_UPDATE_MANIFEST_URL` ignorada no app empacotado.
- `update_helper.py`: revalida `--sha256` antes de extrair, usa `ziputil.safe_extract_zip`, ferramentas do Windows por caminho absoluto, **mantém o backup `.bak-*`** (o app apaga 45 s depois de iniciar: `_cleanup_after_update`).
- `apk_sync.py`: entradas do `apks.json` inválidas (categoria/nome/sha/URL) são descartadas; sem fallback sem hash; nunca apaga o APK bom em falha.
- `ziputil.py`: validação de ZIP (zip-slip, `:`/ADS, nomes reservados, symlink, zip-bomb). Usado no updater, scrcpy, build e instalador.
- `installer/setup_main.py`: instala em pasta de staging e troca no final (rollback se falhar; preserva `capturas`).
- NÃO feito (decisão de produto): ACL restrita em `C:\Aibox`, remover exclusão do Defender/`Unblock-File`, uninstaller fora da pasta gravável, assinatura Authenticode/manifesto assinado, hash fixo do platform-tools no build.
- Testes: `test_security_lote_c.py`, `test_ui_extras.py`, `test_adb_leftovers.py`.
