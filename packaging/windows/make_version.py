"""Gera version.txt (metadados do .exe) a partir de gramdesk/config.py."""
import re
from pathlib import Path

here = Path(__file__).resolve().parent
ver = re.search(r'VERSION = "([\d.]+)"', (here.parents[1] / "gramdesk" / "config.py").read_text()).group(1)
nums = tuple(int(x) for x in (ver.split(".") + ["0", "0", "0"])[:4])
(here / "version.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'Gramdesk'),
      StringStruct('FileDescription', 'Gramdesk - cliente leve e privado para o Instagram'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'Gramdesk'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'Gramdesk.exe'),
      StringStruct('ProductName', 'Gramdesk'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
""", encoding="utf-8")
print(ver)
