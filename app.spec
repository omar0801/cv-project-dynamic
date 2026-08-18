# PyInstaller build spec. Build with: pyinstaller app.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = [
    ('base', 'base'),
    ('modules', 'modules'),
]
datas += collect_data_files('sv_ttk')

# Optional per the README; these collect calls yield nothing if it is not installed.
datas += collect_data_files('tkinterdnd2')
binaries = collect_dynamic_libs('tkinterdnd2')

a = Analysis(
    ['src/app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CVBuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CVBuilder',
)
