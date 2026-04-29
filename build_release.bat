@echo off
setlocal EnableDelayedExpansion
title Smart Keyboard -- Build and Package
cd /d "%~dp0"

echo ================================================================
echo   Smart Keyboard v1.0.0 -- Build and Package
echo ================================================================
echo.

:: ── 1. Activate virtual environment ─────────────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found.
    echo        Run this first:
    echo          python -m venv venv
    echo          venv\Scripts\activate
    echo          pip install -r requirements.txt
    pause
    exit /b 1
)
call venv\Scripts\activate.bat
echo [1/5] Virtual environment activated.

:: ── 2. Build executable ──────────────────────────────────────────────────────
echo.
echo [2/5] Building executable ^(PyInstaller^)...
pyinstaller smart_keyboard.spec --clean -y
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)
echo       Done: dist\SmartKeyboard\SmartKeyboard.exe

:: ── 3. Strip leftover bloat PyInstaller may still include ────────────────────
echo.
echo [3/5] Removing unused files from build...

:: Tkinter / Tcl / Tk -- not used (app uses PyQt5)
for %%D in (_tcl_data _tk_data tcl8) do (
    if exist "dist\SmartKeyboard\_internal\%%D" (
        rd /s /q "dist\SmartKeyboard\_internal\%%D"
        echo       Removed %%D
    )
)
for %%F in (tcl86t.dll tk86t.dll _tkinter.pyd) do (
    if exist "dist\SmartKeyboard\_internal\%%F" (
        del /f /q "dist\SmartKeyboard\_internal\%%F"
        echo       Removed %%F
    )
)

:: Qt5 translations -- English-only UI needs none of these
if exist "dist\SmartKeyboard\_internal\PyQt5\Qt5\translations" (
    rd /s /q "dist\SmartKeyboard\_internal\PyQt5\Qt5\translations"
    echo       Removed PyQt5\Qt5\translations
)

:: lxml -- HTML parser, not used by our code path
if exist "dist\SmartKeyboard\_internal\lxml" (
    rd /s /q "dist\SmartKeyboard\_internal\lxml"
    echo       Removed lxml
)

:: safetensors -- PyTorch weight loader, we use ONNX only
if exist "dist\SmartKeyboard\_internal\safetensors" (
    rd /s /q "dist\SmartKeyboard\_internal\safetensors"
    echo       Removed safetensors
)

:: onnx -- model conversion tool, not needed to run inference (onnxruntime is self-contained)
if exist "dist\SmartKeyboard\_internal\onnx" (
    rd /s /q "dist\SmartKeyboard\_internal\onnx"
    echo       Removed onnx
)

:: google (protobuf) -- pulled in by onnx, goes with it
if exist "dist\SmartKeyboard\_internal\google" (
    rd /s /q "dist\SmartKeyboard\_internal\google"
    echo       Removed google ^(protobuf^)
)

:: markupsafe -- jinja2 dep, jinja2 is already excluded
if exist "dist\SmartKeyboard\_internal\markupsafe" (
    rd /s /q "dist\SmartKeyboard\_internal\markupsafe"
    echo       Removed markupsafe
)

:: PyQt5/Qt5/qml -- QML/QtQuick runtime, app uses QtWidgets only
if exist "dist\SmartKeyboard\_internal\PyQt5\Qt5\qml" (
    rd /s /q "dist\SmartKeyboard\_internal\PyQt5\Qt5\qml"
    echo       Removed PyQt5\Qt5\qml
)

:: PyQt5/Qt5/qsci -- QScintilla code editor component, not used
if exist "dist\SmartKeyboard\_internal\PyQt5\Qt5\qsci" (
    rd /s /q "dist\SmartKeyboard\_internal\PyQt5\Qt5\qsci"
    echo       Removed PyQt5\Qt5\qsci
)

:: PyQt5/bindings -- type binding metadata, not loaded at runtime
if exist "dist\SmartKeyboard\_internal\PyQt5\bindings" (
    rd /s /q "dist\SmartKeyboard\_internal\PyQt5\bindings"
    echo       Removed PyQt5\bindings
)

:: PyQt5/uic -- UI compiler tool, not needed at runtime
if exist "dist\SmartKeyboard\_internal\PyQt5\uic" (
    rd /s /q "dist\SmartKeyboard\_internal\PyQt5\uic"
    echo       Removed PyQt5\uic
)

:: PyQt5 .pyi type stubs -- Python type hints, never loaded at runtime
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command ^
    "Get-ChildItem 'dist\SmartKeyboard\_internal\PyQt5' -Filter *.pyi | Remove-Item -Force"
echo       Removed PyQt5 .pyi type stubs

echo       Cleanup done.

:: ── 4. Copy required AI models into the dist folder ─────────────────────────
echo.
echo [4/5] Copying AI models into package ^(may take a minute^)...

set "MISSING="

if not exist "models\indictrans2\" (
    echo WARNING: models\indictrans2\ not found -- skipping translation model.
    set "MISSING=1"
) else (
    C:\Windows\System32\robocopy.exe "models\indictrans2" "dist\SmartKeyboard\models\indictrans2" /E /NFL /NDL /NJH /NJS /nc /ns /np >nul
    echo       indictrans2 copied.
)

if not exist "models\grammar\coedit-small_int8\" (
    echo WARNING: models\grammar\coedit-small_int8\ not found -- skipping grammar model.
    set "MISSING=1"
) else (
    :: /XF excludes decoder_with_past_model.onnx -- 53 MB file the app never loads
    C:\Windows\System32\robocopy.exe "models\grammar\coedit-small_int8" "dist\SmartKeyboard\models\grammar\coedit-small_int8" /E /XF decoder_with_past_model.onnx /NFL /NDL /NJH /NJS /nc /ns /np >nul
    echo       coedit-small_int8 copied ^(decoder_with_past excluded^).
)

if defined MISSING (
    echo WARNING: Some models were missing -- app may run in degraded mode.
)

:: Copy user manual into the package
if exist "UserManual.txt" (
    copy /y "UserManual.txt" "dist\SmartKeyboard\UserManual.txt" >nul
    echo       UserManual.txt added.
)

:: ── 5. Create ZIP ────────────────────────────────────────────────────────────
echo.
set "ZIP_NAME=SmartKeyboard-v1.0.0-win64.zip"
set "ZIP_PATH=dist\%ZIP_NAME%"
echo [5/5] Creating %ZIP_NAME%...
if exist "%ZIP_PATH%" del /f /q "%ZIP_PATH%"
python -c "import shutil, os; shutil.make_archive(os.path.join('dist','SmartKeyboard-v1.0.0-win64'), 'zip', 'dist', 'SmartKeyboard')"
if errorlevel 1 (
    echo ERROR: Failed to create ZIP.
    pause
    exit /b 1
)

:: ── Optional: compile Inno Setup installer if available ─────────────────────
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
if exist "%ISCC%" (
    echo.
    echo Inno Setup found -- compiling installer...
    "%ISCC%" installer.iss
    if errorlevel 1 (
        echo WARNING: Installer compilation failed, but ZIP is ready.
    ) else (
        echo       Done: dist\SmartKeyboard-v1.0.0-Setup.exe
    )
)

echo.
echo ================================================================
echo   Package ready
echo ================================================================
echo.
echo   ZIP  ^(share this^):  %ZIP_PATH%
echo.
echo   Tell your friends:
echo     1. Extract the ZIP to any folder ^(e.g. Desktop^)
echo     2. Open the SmartKeyboard folder
echo     3. Double-click SmartKeyboard.exe
echo     4. The app runs in the system tray ^(bottom-right clock area^)
echo     5. Press Ctrl+Alt+K anywhere to open the Smart Keyboard popup
echo.
pause
