# InstaScrape macOS App

This folder can be packaged as a double-clickable macOS `.app` that starts the local web UI and opens it in your browser.

## Build (macOS)

```zsh
cd ~/instascrape/build/macos
./build_macos_app.sh
```

Build output:
- `dist/InstaScrape_App/InstaScrape.app`
- `dist/InstaScrape_App/InstaScraper_instructions.txt`

## Run

- In Finder, open `dist/InstaScrape_App/` and double-click `InstaScrape.app`.
- The app starts a local server (prefers `http://127.0.0.1:5055/`, but will automatically use the next free port) and attempts to open it in Google Chrome.

Optional:
```zsh
INSTASCRAPE_PORT=5055 open dist/InstaScrape_App/InstaScrape.app
```

## Notes

- The app runs locally (binds to `127.0.0.1`).
- You still need your Instagram `sessionid` cookie (see `InstaScraper_instructions.txt`).
