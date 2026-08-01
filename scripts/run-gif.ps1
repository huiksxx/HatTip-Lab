$ProjectDir = Split-Path -Parent $PSScriptRoot
& (Join-Path $ProjectDir ".venv\Scripts\python.exe") (Join-Path $ProjectDir "desktop_pet.py") --mode gif @args
