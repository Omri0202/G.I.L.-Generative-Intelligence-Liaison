import sys
import os
import winreg

key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
app_name = "ProjectGIL"
script   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gil.pyw")
exe      = sys.executable.replace("python.exe", "pythonw.exe")
if not os.path.exists(exe):
    exe = sys.executable

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe}" "{script}"')

print(f"  Registered: {exe} {script}")
