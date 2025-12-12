# instascrape

InstaScrape:
- source code
- build scripts (Unix onefile + macOS .app)
- a ready-to-download macOS `.app` folder

## Folder layout
- `source/` – Python source + requirements
- `build/unix/` – builds a single-file Unix/macOS executable via PyInstaller
- `build/macos/` – builds a double-clickable macOS `.app` bundle via PyInstaller
- `downloads/macos/` – prebuilt macOS app folder (for end users)
- `downloads/unix/` – prebuilt onefile executable (optional; for end users)

## Build (Unix onefile)
From this folder:

```zsh
cd build/unix
./build_unix_executable.sh
```

Output:
- `build/unix/dist/instascrape-webui`

## Build (macOS .app)
From this folder:

```zsh
cd build/macos
./build_macos_app.sh
```

Output:
- `build/macos/dist/InstaScrape_App/InstaScrape.app`
- `build/macos/dist/InstaScrape_App/InstaScraper_instructions.txt`

## Download (macOS users)
A ready-to-share folder is included here:
- `downloads/macos/InstaScrape_App/`

Users can double-click:
- `downloads/macos/InstaScrape_App/InstaScrape.app`

## Notes
- The build scripts create their own `.venv/` inside each `build/*` folder.
- For instructions on getting your `sessionid`, see:
  - `source/InstaScraper_instructions.txt`
