@echo off
cd /d "%~dp0"
pyinstaller --onefile --noconsole --icon="%~dp0maimai.ico" --name=maimai_clipper --distpath=..\.. --workpath=..\..\build\work --specpath=..\.. ..\..\setup\launcher.py
if exist "..\..\maimai_clipper.spec" del "..\..\maimai_clipper.spec"
pause
