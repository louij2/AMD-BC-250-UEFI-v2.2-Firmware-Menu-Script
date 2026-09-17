#!/bin/bash
# Build "Fix the console" for the Steam Link and pack it for sideloading.
#
# Produces steamlink/apps/bc250-fix.tgz. Copy the steamlink/ folder to a
# FAT32 USB drive and power cycle the Steam Link, or scp the tgz straight in
# once SSH is enabled on the device.

# Deliberately no `set -u`: the SDK's setenv.sh tests unset variables, so it
# fails immediately under it.

TOP=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ "${MARVELL_SDK_PATH:-}" = "" ]; then
	MARVELL_SDK_PATH="${STEAMLINK_SDK_PATH:-$HOME/steamlink-sdk}"
fi
if [ "${MARVELL_ROOTFS:-}" = "" ]; then
	source "${MARVELL_SDK_PATH}/setenv.sh" || exit 1
fi
cd "${TOP}" || exit 1

APP=bc250-fix

make ${MAKE_J:-} || exit 2

DESTDIR="${TOP}/steamlink/apps/${APP}"
rm -rf "${DESTDIR}"
mkdir -p "${DESTDIR}"

cp -v "${APP}" "${DESTDIR}/" || exit 3
"${TOP}/../common/make_icons.py" "${TOP}/.icons" >/dev/null || exit 3
cp -v "${TOP}/.icons/fix.png" "${DESTDIR}/icon.png" || exit 3

cat >"${DESTDIR}/toc.txt" <<__EOF__
name=Fix the console
icon=icon.png
run=${APP}
__EOF__

# settings.conf carries the BC-250's address and the key to reach it, so it is
# deliberately not in git: this repository is public. Ship the real one if the
# builder has it, otherwise ship the example so the app can explain itself on
# screen rather than failing silently.
if [ -f "${TOP}/settings.conf" ]; then
	cp -v "${TOP}/settings.conf" "${DESTDIR}/settings.conf"
	echo "note: shipping your real settings.conf"
else
	cp -v "${TOP}/settings.example.conf" "${DESTDIR}/settings.conf"
	echo "WARNING: no settings.conf found, shipping the example."
	echo "         The app will show 'Not configured' until you edit it."
fi

# An SSH key for the device, if one has been generated. Same reasoning: never
# in git.
if [ -f "${TOP}/id_steamlink" ]; then
	cp -v "${TOP}/id_steamlink" "${DESTDIR}/id_steamlink"
	chmod 600 "${DESTDIR}/id_steamlink"
fi

cd "$(dirname "${DESTDIR}")" || exit 4
tar zcf "${APP}.tgz" "${APP}" || exit 4
rm -rf "${APP}"

echo
echo "Built $(dirname "${DESTDIR}")/${APP}.tgz"
