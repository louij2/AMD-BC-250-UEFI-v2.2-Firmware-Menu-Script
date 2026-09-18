# Steam Link native apps

Native apps for the original 2015 Steam Link hardware, built with
[Valve's steamlink-sdk](https://github.com/ValveSoftware/steamlink-sdk) and
sideloaded onto the device.

| App | What it does |
|---|---|
| `bc250-fix` | Triggers the BC-250's recovery actions from the TV. |
| `bc250-status` | Read-only health screen: hosts, console cores, LanCache, link latency. |
| `retroarch` | RetroArch front end, pointed at the ROM share. |

Everything is C against SDL2. That is not nostalgia, it is the hardware:
single-core ARMv7 at 1GHz, ~256MB of usable RAM, ~1GB of flash, glibc 2.19,
kernel 3.8. Anything with a modern runtime is out.

## What the device actually offers

Worth writing down, because the numbers in circulation are often wrong and a
couple of them cost real time here.

| | |
|---|---|
| CPU | single-core ARMv7 @ 1GHz, NEON |
| RAM | ~256MB usable |
| Flash | ~1GB usable (not 500MB) |
| Qt | 5.14.1 (not 5.4) |
| SDL | 2.0.27 / 2.0.31, plus `_ttf`, `_image`, `_mixer`, `_net` |
| GL | OpenGL ES 2.0 |
| Board | Marvell BG2CD, codename "eureka" |
| Font on device | `/usr/share/fonts/NotoSans-Regular.ttf` |
| Useful binaries in rootfs | `ssh`, `curl`, busybox (`nc`, `mount`) |

The device having a real `ssh` client and a real `curl` is what makes these
apps small: `bc250-fix` shells out to `ssh` rather than speaking a protocol,
and `bc250-status` shells out to `curl` rather than linking an HTTP stack.

### Network filesystems: NFS, not SMB

From `arch/arm/configs/mv88de3100_ax_bg2cd_eureka_evt_defconfig` in the SDK
kernel tree:

```
CONFIG_NFS_FS=y
CONFIG_NFS_V3=y
CONFIG_CIFS=y
# CONFIG_CIFS_SMB2 is not set
```

CIFS is present but **SMB1 only**. Modern Samba and Unraid refuse SMB1 by
default, so an SMB share is a dead end unless you deliberately re-enable a
protocol nobody should re-enable. **NFSv3 is the supported path** for getting
ROMs onto the device.

## Building

Set the SDK up on a Linux x86-64 host, not on anything you care about — the
toolchain is GCC 9.2.0 from 2019.

```bash
git clone --depth 1 https://github.com/ValveSoftware/steamlink-sdk.git ~/steamlink-sdk
```

That is ~4.7GB checked out. Then, per app:

```bash
cd steamlink-apps/bc250-fix && ./build_steamlink.sh
```

Each `build_steamlink.sh` sources the SDK's `setenv.sh`, cross-compiles, and
packs `steamlink/apps/<app>.tgz`. `STEAMLINK_SDK_PATH` overrides the SDK
location if it is not `~/steamlink-sdk`.

### A trap in the SDK's own scripts

Do not copy the `CC` handling from `examples/retroarch/build_steamlink.sh`. It
does this:

```bash
export CC="${CROSS}gcc"                                    # no sysroot
export CFLAGS="--sysroot=$MARVELL_ROOTFS -DLINUX=1 ..."    # sysroot only here
```

`setenv.sh` puts `--sysroot` **in `CC` itself** for good reason. Configure
scripts that link a test program without passing `CFLAGS` then fail with:

```
ld: cannot find crt1.o: No such file or directory
```

which RetroArch's configure reports as the far less helpful
`armv7a-cros-linux-gnueabi-gcc does not work`. Keep `--sysroot` in `CC` and
`CXX`. The `retroarch/build_steamlink.sh` here does.

### Verifying a build without a Steam Link attached to a TV

You cannot run the ARM binaries under `qemu-arm-static`. The device's SDL2 is
Marvell-specific and initialises display hardware inside `SDL_Init`:

```
bc250-fix: source/linux/user/shm_api_linux.c:111: MV_SHM_Init: Assertion `0' failed.
 SHM Open NONCACHE device no ready, retry 10
```

So layout checking is done by compiling the *same source* natively and using
the `--screenshot <file>` flag both apps support, which renders one frame to a
BMP and exits:

```bash
sudo apt-get install -y libsdl2-dev libsdl2-ttf-dev xvfb
cc -O2 -o /tmp/fix main.c ../common/tvui.c -I../common \
   $(pkg-config --cflags --libs sdl2 SDL2_ttf)
xvfb-run -a /tmp/fix --screenshot /tmp/fix.bmp
```

The Prometheus parsing has its own host-side tests, no SDL needed:

```bash
cd common && cc -O2 -Wall -Wextra -o /tmp/promq_test promq_test.c promq.c -lm
/tmp/promq_test http://your-prometheus:9090     # URL is optional
```

## Installing

The device takes apps from a FAT32 USB drive at boot. Lay it out as:

```
steamlink/
  apps/
    bc250-fix.tgz
    bc250-status.tgz
  config/
    system/
      enable_ssh.txt        <- empty file, enables sshd
```

Insert it and power cycle. The device copies the apps in and registers them in
its menu.

`enable_ssh.txt` turns on sshd with the documented default password
(`root` / `steamlink123`). **Change it immediately** with `passwd` over the
first SSH session. Once SSH is on, later updates skip the USB stick:

```bash
scp bc250-fix.tgz root@steamlink:/home/apps/
```

## Configuration, and why none of it is in git

This repository is public. Every app reads a `settings.conf` from its own
directory, and those files are gitignored; `settings.example.conf` documents
each key.

`bc250-fix` also carries a private SSH key, which is why it is locked down:

- the key is **dedicated**, never a personal one, because a FAT32 stick and a
  device with a published default password are not somewhere secrets live;
- on the BC-250 it is pinned to a forced command, so it can trigger three
  recovery actions and nothing else.

In the BC-250's `~/.ssh/authorized_keys`:

```
restrict,command="/home/YOURUSER/Reboot-to-UEFI/bc250-recovery-remote" ssh-ed25519 AAAA... steamlink-fix
```

The app sends only a bare word — `fix`, `wake` or `reset`. `bc250-recovery-remote`
validates it against exactly those three and refuses everything else, so
`reset; rm -rf ...` is rejected rather than run.

`bc250-status` ships **no** credentials at all: it only makes read-only
Prometheus queries over HTTP.

### Tailscale

The BC-250 is normally reached over Tailscale. The Steam Link cannot run it, so
`settings.conf` uses the BC-250's **LAN** address. That is a deliberate
exception, limited to this one 2015 device.

## bc250-fix

Three actions, all of which already existed on the BC-250 and none of which
are reimplemented here:

| Menu item | Sends | Effect |
|---|---|---|
| Fix it | `fix` | Wakes a stuck sleep if that is the fault, otherwise resets Game Mode. |
| Wake Steam only | `wake` | Non-destructive. Closes nothing. |
| Reset Game Mode | `reset` | Restarts the session; a running game is closed. |

The decision stays on the BC-250, inside `bc250_watchdog.py`, so every guard
rail still applies — including the one that matters most: **no restart within
75s of Steam starting**, because SteamOS moves `~/.steam` aside after three
Steam runs that each last under 60s.

Exit status drives the screen: `0` done, `1` refused or not needed, `255`
could not reach the console. "Not needed" is a distinct outcome from "failed",
and worth seeing — asking to wake a console that is not actually asleep should
not look like a broken app.

## The ROM share

RetroArch reads the library from Unraid's existing **Games** share, which was
already NFS-exported read-only to every host -- so this needed no new share
and no new service. Mount it on the device with:

```bash
mount -t nfs -o vers=3,ro,nolock 10.0.0.24:/mnt/user/Games /mnt/games
```

`vers=3` is not optional, for the CIFS/SMB2 reason above.

BIOS images are in that share, so `system_directory` points at them rather
than copying 117MB into the device's ~1GB of flash. Saves stay in local flash
because the export is read-only -- which is also the safer default: a
misbehaving core cannot write into a 6.4TB library.

## bc250-status

Read-only. Four rows:

| Row | Source |
|---|---|
| Hosts | `sum(up)`, `count(up)`, `count(up == 0)` |
| Console CPU | `count(count by (cpu) (node_cpu_seconds_total{job="bc250"}))` / 2 |
| LanCache | `lancache_cache_hit_ratio` |
| Link to console | measured here, not read from a metric |

Two things worth knowing about those:

**Cores.** SMT is on, so the BIOS core count is half the thread count. 16
threads means the 8-core setting; 12 means 6.

**Latency is measured, not fetched, and that is the whole point.** Steam only
writes a per-session `Ping:` line when the BC-250 is the *client* of a stream.
Every such line in the log is prefixed `CLIENT:`. When the BC-250 is the
*host* — which it is when streaming to this Steam Link — no ping is logged at
all, so `steamlink_rtt_seconds` has never existed in Prometheus, and
`last_over_time(steamlink_rtt_seconds[7d])` returns nothing. The Steam Link is
also not in the `blackbox_icmp` target list, so there is no ICMP figure for it
either.

Rather than display a metric that does not exist, the app times a TCP handshake
to the console from the Steam Link itself. That is the path that actually
carries the stream, measured from the end that actually matters, and the row
says whether it was taken during a live stream or while idle.

`count(up == 0)` returning an empty result is the healthy case, not an error —
`promq` reports "nothing matched" separately from "could not ask" so that
everything-is-up is never rendered as a failure.
