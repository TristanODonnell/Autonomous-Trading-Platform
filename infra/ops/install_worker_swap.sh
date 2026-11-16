#!/usr/bin/env bash
# Give the worker box a swap file (run once, with sudo, from the repo checkout):
#
#   sudo infra/ops/install_worker_swap.sh              # 4G at /swapfile
#   sudo SWAP_SIZE=8G infra/ops/install_worker_swap.sh
#
# Why: with no swap, a research job that needs more memory than the box has is killed with
# no warning. With swap it slows down first, which is visible in a rehearsal, and the memory
# ceiling in worker_job.sh then stops it (exit 137) while the rest of the box keeps running.
# Swap on a network disk is far slower than memory: a safety net, not capacity to plan with.
#
# Safe to re-run: it does nothing when the box already has swap.
set -euo pipefail

SWAP_FILE="${SWAP_FILE:-/swapfile}"
SWAP_SIZE="${SWAP_SIZE:-4G}"

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

if [[ -n "$(swapon --show --noheadings)" ]]; then
  echo "swap is already on, nothing to do:"
  swapon --show
  exit 0
fi

if [[ ! -e "$SWAP_FILE" ]]; then
  fallocate -l "$SWAP_SIZE" "$SWAP_FILE"
  chmod 600 "$SWAP_FILE"
  mkswap "$SWAP_FILE" > /dev/null
fi
swapon "$SWAP_FILE"

# Back after a reboot.
if ! grep -qE "^${SWAP_FILE}[[:space:]]" /etc/fstab; then
  echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
fi

# Use memory first; reach for swap only under pressure (the kernel default is 60).
echo "vm.swappiness=10" > /etc/sysctl.d/99-ratp-swap.conf
sysctl -q -p /etc/sysctl.d/99-ratp-swap.conf

swapon --show
free -h
