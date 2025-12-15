#!/usr/bin/env zsh
set -euo pipefail

cd "${0:A:h}"

SOURCE_DIR="${0:A:h}/../../source"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r "${SOURCE_DIR}/requirements.txt"
python -m pip install pyinstaller

pyinstaller \
  --name instascrape-webui \
  --onefile \
  --noconfirm \
  --clean \
  --collect-all requests \
  --collect-all selenium \
  --collect-all webdriver_manager \
  --add-data "${SOURCE_DIR}/instagram_scraper-0.9.12.py:." \
  --add-data "${SOURCE_DIR}/InstaScraper_instructions.txt:." \
  "${SOURCE_DIR}/instascrape_webui.py"

echo "Built: dist/instascrape-webui"
