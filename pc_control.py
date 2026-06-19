"""
pc_control.py — Project G.I.L.
PC power management and system volume via pycaw.
pip install pycaw comtypes
"""

import ctypes
import subprocess


def pc_sleep() -> str:
    try:
        ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
    except Exception:
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    return "Putting the PC to sleep."


def pc_lock() -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Workstation locked."


def pc_restart() -> str:
    subprocess.run(
        ["shutdown", "/r", "/t", "5"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "Restarting in 5 seconds — save your work."


def pc_shutdown() -> str:
    subprocess.run(
        ["shutdown", "/s", "/t", "5"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "Shutting down in 5 seconds."


def cancel_shutdown() -> str:
    subprocess.run(["shutdown", "/a"], creationflags=subprocess.CREATE_NO_WINDOW)
    return "Shutdown cancelled."


def _get_volume_interface():
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL, CoCreateInstance, GUID
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    enumerator = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
    device    = enumerator.GetDefaultAudioEndpoint(0, 1)   # eRender=0, eMultimedia=1
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_system_volume() -> int:
    try:
        vol = _get_volume_interface()
        return int(vol.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return 50


def set_system_volume(level: int) -> str:
    level = max(0, min(100, level))
    try:
        vol = _get_volume_interface()
        vol.SetMasterVolumeLevelScalar(level / 100, None)
        return f"PC volume set to {level}%."
    except ImportError:
        return "Install pycaw for PC volume control: pip install pycaw comtypes"
    except Exception as exc:
        return f"Volume control error: {exc}"


def mute_system() -> str:
    try:
        vol = _get_volume_interface()
        vol.SetMute(1, None)
        return "PC muted."
    except Exception as exc:
        return f"Mute failed: {exc}"


def unmute_system() -> str:
    try:
        vol = _get_volume_interface()
        vol.SetMute(0, None)
        return "PC unmuted."
    except Exception as exc:
        return f"Unmute failed: {exc}"


def pc_volume_control(command: str) -> str:
    cmd = command.lower().strip()

    if cmd == "mute":
        return mute_system()
    if cmd == "unmute":
        return unmute_system()

    if cmd.startswith("up"):
        try:
            step = int(cmd.split()[-1]) if len(cmd.split()) > 1 else 10
        except ValueError:
            step = 10
        return set_system_volume(get_system_volume() + step)

    if cmd.startswith("down"):
        try:
            step = int(cmd.split()[-1]) if len(cmd.split()) > 1 else 10
        except ValueError:
            step = 10
        return set_system_volume(get_system_volume() - step)

    # "set 40" or bare number
    try:
        level = int(cmd.replace("set", "").replace("%", "").strip())
        return set_system_volume(level)
    except ValueError:
        pass

    return f"Unknown PC volume command: {command}"


def pc_power_control(command: str) -> str:
    cmd = command.lower().strip()
    if cmd == "sleep":    return pc_sleep()
    if cmd == "lock":     return pc_lock()
    if cmd == "restart":  return pc_restart()
    if cmd in ("shutdown", "shut down", "off"): return pc_shutdown()
    if cmd == "cancel":   return cancel_shutdown()
    return f"Unknown PC command: {command}"
