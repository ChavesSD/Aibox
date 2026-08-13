# Aibox

Ferramenta desktop da Intelite para conectar, configurar e manter totens e painéis Android (ADB USB/Wi‑Fi, APKs, DPI, gravação e diagnóstico).

## Repositórios

| Repositório | Uso |
| --- | --- |
| [ChavesSD/Aibox](https://github.com/ChavesSD/Aibox) | Código-fonte deste projeto |
| [ChavesSD/ReleasesAibox](https://github.com/ChavesSD/ReleasesAibox) | Instalador, atualizações do app e catálogo de APKs |

Os APKs **não** entram no instalador. Depois de instalar, o Aibox baixa Totem, Painel e Outros a partir do repositório de releases.

## Desenvolvimento

```powershell
pip install -r aibox/requirements.txt
python run_aibox.py
```

## Compilar o instalador

```powershell
python build_exe.py
```

Saída: `dist\release\Aibox-Setup.exe` (um único arquivo, sem APKs).

## Publicar APKs

```powershell
python publish_apks.py --version 1.0.0 --upload
```

Isso gera `dist\release\apks.json` e envia os APKs para o GitHub Release `apks-1.0.0` em ReleasesAibox. Copie `apks.json` para a raiz desse repositório (`main`) para o app encontrar o catálogo.
