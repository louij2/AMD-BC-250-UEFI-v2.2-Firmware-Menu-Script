#!/usr/bin/env python3
"""Add or update the "BC-250 Recovery" non-Steam shortcut and its artwork.

Steam's shortcuts.vdf is binary VDF. This reads and writes it with no
external dependencies, checks that the file round-trips byte-for-byte before
changing anything, keeps a timestamped backup, and updates an existing entry
in place rather than adding a duplicate.

Steam holds shortcuts in memory and rewrites the file when a shortcut is
launched, so while Steam is running use `install-live`, which goes through
Steam's own API (via the loopback DevTools port) and needs no restart. Use
`install` only when Steam is not running.

Usage:
  steam_shortcut.py list
  steam_shortcut.py install-live INSTALL_DIR   # Steam running
  steam_shortcut.py install INSTALL_DIR        # Steam stopped
  steam_shortcut.py verify INSTALL_DIR
"""
import shutil
import sys
import time
import zlib
from pathlib import Path

APP_NAME = "BC-250 Recovery"
# Names/paths earlier versions of this shortcut used; matched so they get
# updated instead of duplicated.
LEGACY_NAMES = {"BC-250 Recovery Menu", APP_NAME}
MARKER = "Reboot-to-UEFI"

STEAM_ROOT = Path.home() / ".local/share/Steam"

MAP, STR, INT, END = 0x00, 0x01, 0x02, 0x08

FIELD_ORDER = ["appid", "AppName", "Exe", "StartDir", "icon", "ShortcutPath",
               "LaunchOptions", "IsHidden", "AllowDesktopConfig", "AllowOverlay",
               "OpenVR", "Devkit", "DevkitGameID", "DevkitOverrideAppID",
               "LastPlayTime", "FlatpakAppID", "sortas", "tags"]

ART = {  # source file in art/ -> suffix in Steam's grid folder
    "portrait.png": "p.png",
    "wide.png": ".png",
    "hero.png": "_hero.png",
    "logo.png": "_logo.png",
    "icon.png": "_icon.png",
}


# ------------------------------------------------------------ binary VDF ---

def _parse(data, pos):
    result, order = {}, []
    while pos < len(data):
        t = data[pos]
        pos += 1
        if t == END:
            return (result, order), pos
        end = data.index(b"\x00", pos)
        key = data[pos:end].decode("utf-8", errors="surrogateescape")
        pos = end + 1
        if t == MAP:
            value, pos = _parse(data, pos)
        elif t == STR:
            end = data.index(b"\x00", pos)
            value = data[pos:end].decode("utf-8", errors="surrogateescape")
            pos = end + 1
        elif t == INT:
            value = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        else:
            raise ValueError(f"unknown VDF type {t:#x} at offset {pos}")
        result[key] = (t, value)
        order.append(key)
    return (result, order), pos


def _encode(node):
    result, order = node
    out = bytearray()
    for key in order:
        t, value = result[key]
        out.append(t)
        out += key.encode("utf-8", errors="surrogateescape") + b"\x00"
        if t == MAP:
            out += _encode(value)
            out.append(END)
        elif t == STR:
            out += value.encode("utf-8", errors="surrogateescape") + b"\x00"
        elif t == INT:
            out += int(value).to_bytes(4, "little")
    return bytes(out)


def loads(data):
    node, pos = _parse(data, 0)
    if pos != len(data):
        raise ValueError(f"trailing data: parsed {pos} of {len(data)} bytes")
    return node


def dumps(node):
    # the implicit root object has its own closing byte
    return _encode(node) + bytes([END])


# --------------------------------------------------------------- helpers ---

def shortcut_id(exe, name):
    """The 32-bit id Steam uses for grid artwork of a non-Steam shortcut."""
    return (zlib.crc32((exe + name).encode("utf-8")) | 0x80000000) & 0xFFFFFFFF


def userdata_dirs():
    base = STEAM_ROOT / "userdata"
    return sorted(p for p in base.glob("*/config") if p.name == "config" and p.parent.name != "0")


def shortcuts_path(cfg):
    return cfg / "shortcuts.vdf"


def load_file(path):
    data = path.read_bytes() if path.exists() else b""
    if not data:
        return ({"shortcuts": (MAP, ({}, []))}, ["shortcuts"]), data
    node = loads(data)
    if dumps(node) != data:
        raise SystemExit(f"{path}: does not round-trip cleanly; refusing to touch it")
    return node, data


def entries(node):
    return node[0]["shortcuts"][1]


def find_ours(shortcuts):
    result, order = shortcuts
    for idx in order:
        fields = result[idx][1][0]
        name = fields.get("AppName", (STR, ""))[1]
        exe = fields.get("Exe", (STR, ""))[1]
        start = fields.get("StartDir", (STR, ""))[1]
        opts = fields.get("LaunchOptions", (STR, ""))[1]
        if name in LEGACY_NAMES or any(MARKER in s for s in (exe, start, opts)):
            return idx
    return None


def build_entry(install_dir, existing=None):
    exe = f'"{install_dir / "bc250-recovery"}"'
    appid = shortcut_id(exe, APP_NAME)
    if existing and "appid" in existing[0]:
        # keep the id Steam already knows: artwork and play time hang off it
        appid = existing[0]["appid"][1]
    fields = {
        "appid": (INT, appid),
        "AppName": (STR, APP_NAME),
        "Exe": (STR, exe),
        "StartDir": (STR, f'"{install_dir}/"'),
        "icon": (STR, str(install_dir / "art" / "icon.png")),
        "ShortcutPath": (STR, ""),
        "LaunchOptions": (STR, ""),
        "IsHidden": (INT, 0),
        "AllowDesktopConfig": (INT, 1),
        "AllowOverlay": (INT, 1),
        "OpenVR": (INT, 0),
        "Devkit": (INT, 0),
        "DevkitGameID": (STR, ""),
        "DevkitOverrideAppID": (INT, 0),
        "LastPlayTime": (INT, 0),
        "FlatpakAppID": (STR, ""),
        "sortas": (STR, ""),
        "tags": (MAP, ({}, [])),
    }
    order = list(FIELD_ORDER)
    if existing:
        old, old_order = existing
        # keep play time, tags/collections and any fields newer Steam added
        for key in ("LastPlayTime", "tags", "IsHidden"):
            if key in old:
                fields[key] = old[key]
        for key in old_order:
            if key not in fields:
                fields[key] = old[key]
                order.append(key)
    return (fields, order)


# -------------------------------------------------------------- commands ---

def cmd_list():
    for cfg in userdata_dirs():
        path = shortcuts_path(cfg)
        if not path.exists():
            continue
        node, _ = load_file(path)
        result, order = entries(node)
        print(path)
        for idx in order:
            f = result[idx][1][0]
            print(f"  [{idx}] {f['appid'][1]:>10}  {f['AppName'][1]}  ->  {f['Exe'][1]}")


def cmd_install(install_dir):
    """Edit shortcuts.vdf directly. Only safe while Steam is NOT running."""
    install_dir = install_dir.resolve()
    dirs = userdata_dirs()
    if not dirs:
        raise SystemExit("no Steam userdata found; log in to Steam once first")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for cfg in dirs:
        path = shortcuts_path(cfg)
        node, original = load_file(path)
        shortcuts = entries(node)
        result, order = shortcuts
        idx = find_ours(shortcuts)
        if idx is None:
            idx = str(max((int(i) for i in order), default=-1) + 1)
            result[idx] = (MAP, build_entry(install_dir))
            order.append(idx)
            action = "added"
        else:
            result[idx] = (MAP, build_entry(install_dir, result[idx][1]))
            action = "updated"
        sid = result[idx][1][0]["appid"][1]
        new = dumps(node)
        check = entries(loads(new))
        assert check[0][idx][1][0]["AppName"][1] == APP_NAME
        assert len(check[1]) == len(order)
        if original:
            path.with_name(f"shortcuts.vdf.bak-{stamp}").write_bytes(original)
        tmp = path.with_suffix(".vdf.tmp")
        tmp.write_bytes(new)
        tmp.replace(path)
        print(f"{path}: {action} entry [{idx}] appid {sid}")

        grid = cfg / "grid"
        grid.mkdir(exist_ok=True)
        for src, suffix in ART.items():
            s = install_dir / "art" / src
            if s.exists():
                shutil.copyfile(s, grid / f"{sid}{suffix}")
        print(f"{grid}: artwork installed as {sid}*")


# SetCustomArtworkForApp asset types
ASSET_TYPES = {"portrait.png": 0, "hero.png": 1, "logo.png": 2, "wide.png": 3}


def cmd_install_live(install_dir):
    """Update the shortcut through Steam's own API while Steam is running."""
    import base64
    import json

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import steam_cdp

    install_dir = install_dir.resolve()
    exe = f'"{install_dir / "bc250-recovery"}"'
    start = f'"{install_dir}/"'
    icon = str(install_dir / "art" / "icon.png")

    appid = None
    for cfg in userdata_dirs():
        path = shortcuts_path(cfg)
        if path.exists():
            node, _ = load_file(path)
            shortcuts = entries(node)
            idx = find_ours(shortcuts)
            if idx is not None:
                appid = shortcuts[0][idx][1][0]["appid"][1]
                break

    if appid is None:
        appid = steam_cdp.evaluate(
            f"SteamClient.Apps.AddShortcut({json.dumps(APP_NAME)}, {json.dumps(exe)}, "
            f"{json.dumps(start)}, '')")
        print(f"added shortcut, Steam assigned appid {appid}")
    else:
        print(f"updating existing shortcut appid {appid}")

    calls = [
        f"SteamClient.Apps.SetShortcutName({appid}, {json.dumps(APP_NAME)})",
        f"SteamClient.Apps.SetShortcutExe({appid}, {json.dumps(exe)})",
        f"SteamClient.Apps.SetShortcutStartDir({appid}, {json.dumps(start)})",
        f"SteamClient.Apps.SetShortcutLaunchOptions({appid}, '')",
        f"SteamClient.Apps.SetShortcutIcon({appid}, {json.dumps(icon)})",
    ]
    for src, asset in ASSET_TYPES.items():
        data = base64.b64encode((install_dir / "art" / src).read_bytes()).decode()
        calls.append(f"SteamClient.Apps.SetCustomArtworkForApp({appid}, '{data}', 'png', {asset})")
    for js in calls:
        steam_cdp.evaluate(js, timeout=15)
    print(f"name, launcher, icon and {len(ASSET_TYPES)} artwork images applied")
    return appid


def cmd_verify(install_dir):
    install_dir = install_dir.resolve()
    exe = f'"{install_dir / "bc250-recovery"}"'
    ok = True
    for cfg in userdata_dirs():
        path = shortcuts_path(cfg)
        node, _ = load_file(path)
        result, order = entries(node)
        ours = [result[i][1][0] for i in order
                if result[i][1][0]["AppName"][1] in LEGACY_NAMES]
        if len(ours) != 1:
            print(f"FAIL {path}: expected 1 BC-250 entry, found {len(ours)}")
            ok = False
            continue
        e = ours[0]
        sid = e["appid"][1]
        if e["Exe"][1] != exe or e["AppName"][1] != APP_NAME:
            print(f"FAIL {path}: entry not updated yet "
                  f"(name={e['AppName'][1]!r}, exe={e['Exe'][1]})")
            ok = False
        grid = cfg / "grid"
        missing = [src for src, asset in ASSET_TYPES.items()
                   if not any(grid.glob(f"{sid}{ART[src].rsplit('.', 1)[0]}.*"))]
        if missing:
            print(f"FAIL {grid}: no artwork for {missing} (appid {sid})")
            ok = False
        if ok:
            print(f"OK {path}: {len(order)} shortcuts, '{APP_NAME}' appid {sid}, "
                  f"launcher {e['Exe'][1]}, artwork present")
    return ok


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "install" and len(sys.argv) == 3:
        cmd_install(Path(sys.argv[2]))
    elif cmd == "install-live" and len(sys.argv) == 3:
        cmd_install_live(Path(sys.argv[2]))
    elif cmd == "verify" and len(sys.argv) == 3:
        sys.exit(0 if cmd_verify(Path(sys.argv[2])) else 1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
