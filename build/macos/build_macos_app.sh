#!/usr/bin/env zsh
set -euo pipefail

cd "${0:A:h}"

SOURCE_DIR="${0:A:h}/../../source"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r "${SOURCE_DIR}/requirements.txt"
python -m pip install pyinstaller

# Build a double-clickable macOS app bundle.
# Note: data separator is ':' on macOS/Linux.
pyinstaller \
  --name InstaScrape \
  --windowed \
  --onedir \
  --noconfirm \
  --clean \
  --collect-all requests \
  --collect-all selenium \
  --collect-all webdriver_manager \
  --add-data "${SOURCE_DIR}/instagram_scraper-0.9.12.py:." \
  --add-data "${SOURCE_DIR}/InstaScraper_instructions.txt:." \
  "${SOURCE_DIR}/instascrape_webui.py"

# Create a single folder you can zip/share.
rm -rf "dist/InstaScrape_App"
mkdir -p "dist/InstaScrape_App"

mv "dist/InstaScrape.app" "dist/InstaScrape_App/"
cp "${SOURCE_DIR}/InstaScraper_instructions.txt" "dist/InstaScrape_App/"

cat <<'EOF'

Built:
  dist/InstaScrape_App/InstaScrape.app

To run:
  Double-click InstaScrape.app in Finder.
EOF
