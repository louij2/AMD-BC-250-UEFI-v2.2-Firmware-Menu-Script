#!/bin/bash
# Build RetroArch and a set of libretro cores for the Steam Link.
#
# This exists instead of using the SDK's own examples/retroarch/build_steamlink.sh
# for two reasons:
#
#  1. That script is broken. It sets CC without --sysroot and puts --sysroot in
#     CFLAGS only, so RetroArch's configure cannot link a test program and
#     reports "armv7a-cros-linux-gnueabi-gcc does not work". The real error,
#     three layers down, is "ld: cannot find crt1.o".
#  2. It builds the front end and no cores at all, so the result cannot run a
#     single ROM. The cores are most of the work.
#
# Deliberately no `set -u`: the SDK's setenv.sh tests unset variables.

TOP=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ "${MARVELL_SDK_PATH:-}" = "" ]; then
	MARVELL_SDK_PATH="${STEAMLINK_SDK_PATH:-$HOME/steamlink-sdk}"
fi
if [ "${MARVELL_ROOTFS:-}" = "" ]; then
	source "${MARVELL_SDK_PATH}/setenv.sh" || exit 1
fi

APP=retroarch
WORK="${STEAMLINK_WORK:-$HOME/.cache/steamlink-retroarch}"
SRC="${WORK}/RetroArch"
CORES="${WORK}/cores"
JOBS=${MAKE_JOBS:-$(nproc)}

# Keep --sysroot inside CC/CXX, not only in CFLAGS. This is the entire fix.
export OS=Linux
export CC="${CROSS}gcc --sysroot=${MARVELL_ROOTFS}"
export CXX="${CROSS}g++ --sysroot=${MARVELL_ROOTFS}"
export CPP="${CROSS}cpp --sysroot=${MARVELL_ROOTFS}"

mkdir -p "${WORK}" "${CORES}" || exit 1

# ---------------------------------------------------------------- front end

if [ ! -d "${SRC}" ]; then
	echo ">> cloning RetroArch"
	git clone --depth 1 -q https://github.com/libretro/RetroArch.git "${SRC}" || exit 2
fi

if [ ! -x "${SRC}/retroarch" ]; then
	echo ">> configuring RetroArch"
	(
		cd "${SRC}" || exit 2
		export CFLAGS="--sysroot=${MARVELL_ROOTFS} -DLINUX=1 -DEGL_API_FB=1"
		export LDFLAGS="--sysroot=${MARVELL_ROOTFS} ${MARVELL_RPATH_LINK_OPTIONS} -lEGL -lpthread"
		export INCLUDE_DIRS="-I${MARVELL_ROOTFS}/usr/include -I${MARVELL_ROOTFS}/usr/include/EGL -I${MARVELL_ROOTFS}/usr/include/SDL2 -I${MARVELL_ROOTFS}/include/GLES2 -I${MARVELL_ROOTFS}/usr/include/freetype2"
		export LIBRARY_DIRS="-L${MARVELL_ROOTFS}/usr/lib -L${MARVELL_ROOTFS}/lib"

		# --disable-threads matches the SDK example and this single core; it
		# also disables Vulkan, which the hardware does not have anyway.
		./configure --host="${SOC_BUILD}" \
			--disable-threads --disable-alsa --disable-pulse \
			--enable-neon --disable-shaderpipeline --enable-opengles \
			>/dev/null 2>&1 || exit 2
	) || { echo "configure failed"; exit 2; }

	echo ">> building RetroArch (this takes a while)"
	make -C "${SRC}" -j"${JOBS}" >"${WORK}/frontend.log" 2>&1 || {
		echo "build failed, see ${WORK}/frontend.log"
		tail -20 "${WORK}/frontend.log"
		exit 2
	}
fi

# -------------------------------------------------------------------- cores
#
# Chosen for what a single-core 1GHz ARMv7 with 256MB can actually run at full
# speed. Anything 3D, and most of the 32-bit CD generation, does not belong on
# this box -- the BC-250 is there for that, and streaming it is the whole
# reason the Steam Link is plugged in. snes9x2005 rather than a current snes9x
# for the same reason: it was written for hardware like this.

# Fields: name|repo|recurse|makefile|label
#
# recurse=1 for cores that keep parts of themselves in submodules; a plain
# --depth 1 clone leaves those empty and the build dies on a missing header
# (picodrive: "dr_libs/dr_mp3.h: No such file or directory").
#
# picodrive is deliberately absent. Its build wants its own ./configure run
# before make and fails at "config.mak Error 1" otherwise, and the only thing
# it would add over genesis_plus_gx is 32X -- which one 1GHz ARMv7 core is not
# going to run at full speed anyway. Not worth the extra build step.
#
# gpsp rather than mgba for GBA. mgba upstream is CMake-only now and has no
# libretro Makefile at all, but the real reason is the hardware: mgba is a
# cycle-accurate core and this is one 1GHz ARMv7. gpsp was written for
# handhelds of roughly this power and actually holds full speed.

CORE_LIST="
fceumm|https://github.com/libretro/libretro-fceumm.git|0|Makefile.libretro|NES
snes9x2005|https://github.com/libretro/snes9x2005.git|0||SNES
genesis_plus_gx|https://github.com/libretro/Genesis-Plus-GX.git|0|Makefile.libretro|Mega Drive, Master System, Game Gear
gambatte|https://github.com/libretro/gambatte-libretro.git|0||Game Boy, Game Boy Color
gpsp|https://github.com/libretro/gpsp.git|0||Game Boy Advance
"

echo ">> building cores"
built=0
failed=""

echo "$CORE_LIST" | while IFS='|' read -r name repo recurse mkfile label; do
	[ -z "$name" ] && continue
	dir="${CORES}/${name}"

	if [ ! -d "$dir" ]; then
		if [ "$recurse" = "1" ]; then
			git clone --depth 1 --recurse-submodules --shallow-submodules \
				-q "$repo" "$dir" || { echo "   clone failed: $name"; continue; }
		else
			git clone --depth 1 -q "$repo" "$dir" || { echo "   clone failed: $name"; continue; }
		fi
	elif [ "$recurse" = "1" ] && [ -f "$dir/.gitmodules" ]; then
		# A tree cloned earlier without submodules would fail the same way.
		git -C "$dir" submodule update --init --recursive --depth 1 -q 2>/dev/null
	fi

	# Cores disagree about whether the libretro makefile is the default one.
	mk=""
	if [ -n "$mkfile" ] && [ -f "$dir/$mkfile" ]; then
		mk="-f $mkfile"
	elif [ -f "$dir/Makefile.libretro" ] && [ ! -f "$dir/Makefile" ]; then
		mk="-f Makefile.libretro"
	fi

	if make -C "$dir" $mk -j"${JOBS}" platform=unix \
		>"${WORK}/core-${name}.log" 2>&1 && \
		ls "$dir"/${name}_libretro.so >/dev/null 2>&1; then
		echo "   ok      ${name}  (${label})"
	else
		echo "   FAILED  ${name}  -- see ${WORK}/core-${name}.log"
	fi
done

# ------------------------------------------------------------------ package

DESTDIR="${TOP}/steamlink/apps/${APP}"
rm -rf "${DESTDIR}"
mkdir -p "${DESTDIR}/.home/.config/retroarch"

cp -v "${SRC}/retroarch" "${DESTDIR}/" || exit 3
${STRIP} "${DESTDIR}/retroarch"

# Cores live beside the binary: the device installs apps to /home/apps/<app>,
# and retroarch.cfg points libretro_directory there.
n=0
for so in "${CORES}"/*/*_libretro.so; do
	[ -f "$so" ] || continue
	cp "$so" "${DESTDIR}/" && ${STRIP} "${DESTDIR}/$(basename "$so")" && n=$((n + 1))
done
echo ">> packaged ${n} core(s)"

if [ "$n" -eq 0 ]; then
	echo "WARNING: no cores were packaged. RetroArch will start but cannot"
	echo "         load any content. Check ${WORK}/core-*.log."
fi

cp -v "${TOP}/retroarch.cfg" "${DESTDIR}/.home/.config/retroarch/retroarch.cfg" || exit 3

# Anything the builder dropped in extra/ (cores built elsewhere, BIOS files,
# assets) rides along.
if [ -d "${TOP}/extra" ]; then
	cp -v -r "${TOP}/extra/." "${DESTDIR}/"
fi

cat >"${DESTDIR}/toc.txt" <<__EOF__
name=RetroArch
icon=icon.png
run=retroarch
__EOF__

"${TOP}/../common/make_icons.py" "${TOP}/.icons" >/dev/null 2>&1
if [ -f "${TOP}/icon.png" ]; then
	cp -v "${TOP}/icon.png" "${DESTDIR}/icon.png"
else
	# No bespoke icon yet; the status tile is a reasonable stand-in and the
	# device requires the file to exist.
	cp -v "${TOP}/.icons/status.png" "${DESTDIR}/icon.png"
fi

cd "$(dirname "${DESTDIR}")" || exit 4
tar zcf "${APP}.tgz" "${APP}" || exit 4
du -h "${APP}.tgz"
rm -rf "${APP}"

echo
echo "Built $(dirname "${DESTDIR}")/${APP}.tgz"
echo "RetroArch $(cd "${SRC}" && git rev-parse --short HEAD)"
