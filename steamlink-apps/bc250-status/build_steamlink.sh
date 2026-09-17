#!/bin/bash
# Build the homelab status screen for the Steam Link and pack it for sideloading.
#
# Produces steamlink/apps/bc250-status.tgz. Copy the steamlink/ folder to a
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

APP=bc250-status

make ${MAKE_J:-} || exit 2

DESTDIR="${TOP}/steamlink/apps/${APP}"
rm -rf "${DESTDIR}"
mkdir -p "${DESTDIR}"

cp -v "${APP}" "${DESTDIR}/" || exit 3
"${TOP}/../common/make_icons.py" "${TOP}/.icons" >/dev/null || exit 3
cp -v "${TOP}/.icons/status.png" "${DESTDIR}/icon.png" || exit 3

cat >"${DESTDIR}/toc.txt" <<__EOF__
name=Homelab status
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

# No SSH key here on purpose: this app only reads Prometheus over HTTP, so it
# has no need for credentials and should not carry any.

cd "$(dirname "${DESTDIR}")" || exit 4
tar zcf "${APP}.tgz" "${APP}" || exit 4
rm -rf "${APP}"

echo
echo "Built $(dirname "${DESTDIR}")/${APP}.tgz"
