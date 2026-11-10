#!/usr/bin/env bash
# ============================================================================
#  WLAB :: удаление
#  sudo bash uninstall.sh            убрать программу, конфиг оставить
#  sudo bash uninstall.sh --purge    убрать и конфиг, и логи
# ============================================================================
set -euo pipefail

DEST=/opt/wlab
ETC=/etc/wlab
STATE=/var/log/wlab
BINLINK=/usr/local/bin/wlab

PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) echo "Использование: sudo bash uninstall.sh [--purge]"; exit 0 ;;
    *) echo "неизвестная опция: $a"; exit 1 ;;
  esac
done

[[ ${EUID} -eq 0 ]] || { echo "[-] запускай через sudo"; exit 1; }

say() { printf '\033[36m[*]\033[0m %s\n' "$*"; }

if [[ -x "$DEST/bin/wlab-stop" ]]; then
  say "останавливаю сервисы..."
  "$DEST/bin/wlab-stop" >/dev/null 2>&1 || true
fi

say "убираю $DEST"
rm -rf "$DEST"
rm -f "$BINLINK"
rm -f /etc/udev/rules.d/99-wlab-nrf.rules
udevadm control --reload-rules 2>/dev/null || true

if [[ $PURGE -eq 1 ]]; then
  say "убираю конфиг и логи"
  rm -rf "$ETC" "$STATE"
else
  say "конфиг оставлен: $ETC/wlab.conf"
  say "логи оставлены:  $STATE"
fi

say "готово"
