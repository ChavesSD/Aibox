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

## Lote D — pacote e release (07/10/2026)
- **ffmpeg embutido**: `build_exe._ensure_ffmpeg()` baixa o essentials 8.1 (URL e SHA-256 fixos em `build_exe.py`) para `aibox/ffmpeg/` (gitignored: só `ffmpeg.exe` + licença GPL). No app empacotado `recording._resolve_ffmpeg()` usa SÓ o ffmpeg do pacote (sem PATH/env/cwd). Não há ffprobe: `_ffmpeg_info()` lê duração/áudio de `ffmpeg -i`. Antes, sem ffmpeg/ffprobe na máquina, a gravação inteira falhava ("MP4 incompleto").
- **Build**: `build_exe.py` exige `release_notes/<APP_VERSION>.json` válido (version igual, `published_at` AAAA-MM-DD, ao menos uma mudança; `AIBOX_ALLOW_NO_NOTES=1` p/ teste). `AIBOX_REQUIRE_SIGN=1` aborta se o exe/instalador não for assinado. Validar o spec sem tocar em processos: `python -m PyInstaller --noconfirm --clean --distpath <tmp>/dist --workpath <tmp>/build Aibox.spec` (NÃO use build_exe.py para isso: ele faz taskkill de adb/Aibox).
- **Atualização/instalação preservam `Apks/`** (`update_helper.preserve_user_data`, `setup_main`).
- Pós-instalação sempre reabilita o Autostart (`set_autostart_apps_enabled(True)` no `finally`), mesmo se a Síntese de Voz falhar.
- Smoke do EXE congelado sem mexer no adb do dev: `ANDROID_ADB_SERVER_PORT=5999 QT_QPA_PLATFORM=offscreen Aibox.exe`.

## Lote E — Síntese de Voz / Autostart (07/10/2026, NÃO validado em aparelho)
Só validações aditivas: o caminho que já funcionava nos Mini PCs continua devolvendo o mesmo resultado.
- `_tts_navigate_installer`: dump vazio é refeito (3x); Voz V visível sem marca → toca nela e reconfere; se ainda não der para verificar, devolve sucesso mas com `voz-v-nao-verificada` no trail e `ATENÇÃO` na mensagem. Após o 1º Enter, tela `unknown` é reclassificada (até 3x) antes de decidir.
- `_ui_turn_on_autostart`: dump vazio nunca gera toque (antes tocava com XML velho e desligava o que ligou).
- `_ui_tap_label_anywhere`: texto com < 4 letras não "contém" o rótulo por acaso.
- `_configure_autostart_app_ui`: confere a tela principal antes do BACK; reprova só se a tela estiver visível e o app ausente (senão registra "conferência indisponível").
- Animações (`_tts_prepare_fast_ui`/`_tts_restore_animations`) e brilho/fb (`_tts_restore_display`) são restaurados; `monkey` com `Events injected: 0` cai no `am start`; `/sdcard/aibox_ui.xml` é removido.
- Pendente (precisa de aparelho): cancelamento/prazo total, Enter duplo, código morto `_tts_*`/`_ui_navigate_*` sem chamador.
- Testes: `test_lote_d.py`, `test_lote_e.py` (188 no total).

## Áudio do equipamento no Android 10 — sndcpy (08/10/2026, validado em aparelho)
- Causa do "áudio não capturado": o Mini PC de teste (192.168.2.91, Allwinner H616) é **Android 10**; scrcpy só captura áudio no 11+ e o aparelho não tem `tinycap` (shell sem root). O MP4 saía com a faixa de silêncio de compatibilidade (-91 dB).
- Solução: `aibox/sndcpy_audio.py` (sem Qt, testável) usa o APK do **sndcpy 1.1** (MIT; SHA-256 fixados; baixado por `build_exe._ensure_sndcpy()` para `aibox/sndcpy/`, gitignored): `install -t -r -g` (pulado se a versão/permissão já estão ok) → `appops set com.rom1v.sndcpy PROJECT_MEDIA allow` (sem diálogo na tela) → `adb forward tcp:<livre> localabstract:sndcpy` → `am start …/.MainActivity` → lê PCM s16le 48 kHz estéreo → WAV. Ordem em `DeviceAudioRecordThread`: scrcpy (SDK>=30) → sndcpy (SDK>=29) → tinycap.
- O **vídeo só começa quando o áudio está entrando** (`ready` → `MainWindow._begin_video_recording`; prazo 35 s; falha do áudio = grava só o vídeo e avisa). A preparação leva ~7 s na 1ª vez (instala o app) e ~2 s depois. `mux_audio_into_mp4` aceita atraso negativo (corta o início do áudio).
- Teste ponta a ponta feito pelo próprio `MainWindow` contra o aparelho real: MP4 com áudio real (-19,7 dB médio, som já no 1º segundo). Sincronia fina áudio/vídeo (±0,5 s, `-0.6` s de latência do screenrecord) ainda sem conferência visual.
- Testes: `test_sndcpy_audio.py` (servidor TCP falso + adb falso).

## Síntese de Voz — verificação do idioma (13/10/2026)
- Defeito achado no aparelho real: a receita "38× baixo" pode parar em **polonês (Polônia)** (a 1ª seta às vezes só foca o 1º item da lista) e o app dava "Voz V confirmada". Agora `_tts_ensure_portuguese_brazil` lê o TÍTULO da tela "<idioma> (<país>) vozes" (`xml_voice_language_title`); se não for português (Brasil): BACK (o foco permanece no item), 1 seta para o lado certo (comparação alfabética), Enter e reconfere (até 4 correções). Se não conseguir, **retorna falha** (`idioma-errado=<idioma>`) em vez de marcar a Voz V de outro idioma; a tela final também é conferida.
- Texto do Otimizar: o aumento de disco é atribuído ao dexopt quando ele rodou.
- Testes: `test_tts_idioma.py` (227 no total). **Validação ao vivo INCONCLUSIVA**: nas execuções ao vivo seguintes o app viu `apos-enter=unknown` (dump parecia ser do launcher) e o aparelho caiu da rede logo depois; repetir quando voltar.

## Validação completa em aparelho real (14/10/2026) e correções
- Voz: com a voz pt-BR **já instalada**, o foco ao abrir o idioma cai na LIXEIRA (se não estiver instalada é o botão de baixar) e o 2º toque do "Enter duplo" abre "Desinstalar esta voz?" (MANTER/DESINSTALAR). Antes o app seguia às cegas (224 s e risco de desinstalar). Agora `xml_tts_uninstall_dialog` + `_tts_dismiss_uninstall_dialog` (só BACK = cancelar; nunca confirma) → trail `voz-ja-instalada`, pula a espera de download (36 s). Validado: voz instalada, seta perdida (polonês → corrige para português), download com a voz desinstalada, e instalação completa pela UI com Autostart (Upzz: "conferido na tela", props de boot aplicadas).
- Neste aparelho o `dump` NÃO mostra `checked` do rádio da Voz V (a imagem mostra marcado, o XML diz false) — por isso `voz-v-nao-verificada` pode aparecer mesmo com a voz certa.
- Dump a cada seta "congela" a lista (1 dump em 70 setas moveu 4 itens): não navegar lendo foco passo a passo; manter a receita cega + verificação pelo TÍTULO.
- Gravação de tela estática: `screenrecord` só emite quadros quando a imagem muda → 1 quadro, duração N/A, o app rejeitava como "incompleto". Agora `_is_single_frame_recording` + `_extend_static_recording` (extrai o quadro e repete até o tempo gravado). Testes: `test_static_screen.py` (238 no total).
- Publicação inconsistente (fora do app): em 13/10 os APKs Totem/Painel "Atendimento_Inteligente" foram trocados no repositório de releases sem regenerar `apks.json` → hash não confere → o app recusa (correto), mas os técnicos não conseguem baixar. Regenerar com `publish_apks.py`.
- Sobras inevitáveis sem root: props `persist.sys.boot*` ficam com valor vazio após o teste de Autostart.

## Reset de fábrica real (14/10/2026, autorizado pelo usuário no aparelho de teste 192.168.2.91)
- A tela "Redefinir" (`com.android.tv.settings/.device.storage.ResetActivity`, botões Cancelar/Redefinir) **não mostra nenhum item focado no dump** até uma seta; a trava do Lote A (rótulo desconhecido = não aperta Enter) abortava. Agora, sem foco visível, `_confirm_factory_reset_on_ui` dá até 2 setas só para focar e **continua só apertando Enter em «Redefinir»/«Apagar tudo»** (nunca «Cancelar»/desconhecido).
- Com isso o reset foi confirmado pela UI do app e o aparelho caiu da rede. O app terminou (49 s) ANTES de o aparelho cair e mostrou "tentativa de confirmação enviada, conexão ADB mantida" — mensagem tecnicamente correta no momento, mas o reset estava em andamento (pode valer esperar mais que 20 s antes de concluir).
- Depois do reset o Android perde o Wi-Fi e o ADB: precisa de acesso físico para reconfigurar.
- Testes: `test_safety_lote_a.py` (240 no total).

- **Mensagem pós-reset (14/10/2026):** o monitor Wi-Fi (`_wifi_monitor_tick`) tentava reconectar o aparelho que reiniciava e, após 3 falhas, mostrava o erro técnico cru ("cannot connect to … (10060)"). Agora, durante/logo após um reset pedido (`_reset_requested_at`, janela de 15 min) o monitor NÃO reconecta e mostra uma única mensagem amigável (`_notify_reset_in_progress`: o aparelho desconectou, é esperado, ele perde Wi‑Fi/depuração, reconecte depois de configurar). A queda de conexão comum também ficou amigável, com o erro técnico por último. Testes: `test_reset_message.py` (245 no total).

## Atualizar APKs no repositório de releases (14/10/2026)
- Ao trocar um APK em `ChavesSD/ReleasesAibox/apk_assets/` é OBRIGATÓRIO regenerar e publicar o `apks.json` (sha256/size): sem isso o Aibox recusa o download ("Checksum inválido", correto). Em 13/10 isso ocorreu com os dois "Atendimento_Inteligente"; corrigido com o catálogo 1.0.3 (commit `c783a15` do repo de releases).
- Receita: reconstruir a pasta `Totem|Painel|Outros` a partir dos arquivos do repo, `publish_apks.build_manifest(src, "X.Y.Z")`, commit do `apks.json`. Conferir com **`python publish_apks.py --verify`** (baixa cada APK do catálogo publicado e compara o hash). O cache do raw.githubusercontent pode atrasar o catálogo novo por alguns minutos.
- O app agora explica a falha ("apks.json desatualizado… regenerar com publish_apks.py") em vez de mostrar Esperado/Obtido (`apk_sync.friendly_sync_error`). Testes: `test_apk_sync_msg.py` (248 no total).
