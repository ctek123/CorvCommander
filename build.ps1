# Windows build (PowerShell)
if (!(Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -U pip wheel
pip install -r requirements.txt
pyinstaller --onefile --name VetteBee-Comms --add-data "config.toml;." beevette.py
Write-Host "Built dist\VetteBee-Comms.exe"
