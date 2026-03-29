# Streamlink GUI Recorder

A PyQt6 application to record livestreams using [Streamlink](https://streamlink.github.io/). Recordings are automatically converted to MP4 using [FFmpeg](https://ffmpeg.org/).

## System requirements

- Python 3.8 or newer
- Streamlink available in your `PATH`
- FFmpeg available in your `PATH`
- PyQt6 (installed via `pip`)

Ubuntu/Debian users can install the required system packages with:

```sh
apt-get update
apt-get install ffmpeg libegl1 libegl-mesa0
```

## Installation

```sh
git clone <repo-url>
cd Novo-Come-o
pip install -r requirements.txt
```

## Running

Launch the GUI with:

```sh
python streamlink_gui_recorder.py
```

On Windows you may use `executar.bat`.

## Basic usage

1. **Manual recording** – add a label and live URL in the first tab and click **Gravar**. Use **Encerrar** to stop and convert the file.
2. **Channel monitoring** – add channels in the second tab. Active channels start recording automatically when they go live.
3. **Telegram tab** – fill in your bot token and chat ID to receive start/finish notifications.

Recordings are stored in the `GRAVACOES MANUAIS` and `MONITORAMENTO` folders defined in the configuration.

## Configuration file

Settings are stored in `config.json`:

```json
{
  "output_dir_manual": "E:\\STREAMLINK\\GRAVACOES MANUAIS",
  "output_dir_monitor": "E:\\STREAMLINK\\MONITORAMENTO",
  "monitored": [],
  "telegram_token": "...",
  "telegram_chat_id": "..."
}
```

You can edit this file directly or save changes through the GUI. Telegram credentials may also be provided via the environment variables `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`.

## External tools

The recorder relies on Streamlink for downloads and FFmpeg for conversion. Ensure both commands work from the terminal:

```sh
streamlink --version
ffmpeg -version
```

