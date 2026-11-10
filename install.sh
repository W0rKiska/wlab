#!/usr/bin/env bash
# ============================================================================
#  WLAB :: установщик
#  git clone <repo> && cd wlab && sudo bash install.sh
# ============================================================================
set -euo pipefail

SRC=$(cd "$(dirname "$0")" && pwd)
DEST=/opt/wlab
ETC=/etc/wlab
STATE=/var/log/wlab
BINLINK=/usr/local/bin/wlab

NO_DEPS=0
NO_UDEV=0
CLEAN_V9=0

usage() {
  cat <<EOF
Установка WLAB

  sudo bash install.sh               поставить всё + зависимости
  sudo bash install.sh --no-deps     без apt (если пакеты уже есть)
  sudo bash install.sh --no-udev     не ставить udev-правило для nRF52840
  sudo bash install.sh --clean-v9    сначала снести остатки старой версии (v9)
  sudo bash install.sh --uninstall   снести
EOF
}

for a in "$@"; do
  case "$a" in
    --no-deps)   NO_DEPS=1 ;;
    --no-udev)   NO_UDEV=1 ;;
    --clean-v9)  CLEAN_V9=1 ;;
    --uninstall) exec "$SRC/uninstall.sh" ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "неизвестная опция: $a"; usage; exit 1 ;;
  esac
done

[[ ${EUID} -eq 0 ]] || { echo "[-] запускай через sudo"; exit 1; }

say() { printf '\033[36m[*]\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m[+]\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m[!]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
#  остатки v9: команды в PATH, старый конфиг, /tmp-мусор, живые процессы
# ---------------------------------------------------------------------------
if [[ $CLEAN_V9 -eq 1 ]]; then
  say "чищу остатки v9..."

  V9_CMDS="wcop Wscan Wscan.sh Wdeaut Wdeaut.sh Watak Watak.sh bscan bpak sscan sscan.sh stop wclog wtaak wpoisk wcop-help batak"

  for c in $V9_CMDS; do
    for d in /usr/local/bin /usr/bin /bin "$HOME/bin"; do
      if [[ -f "$d/$c" ]]; then
        rm -f "$d/$c"
        ok "удалён $d/$c"
      fi
    done
  done

  # старые процессы, чтобы не держали адаптеры
  pkill -f 'python3.*wcop'        2>/dev/null || true
  pkill -f 'hostapd.*wcop'        2>/dev/null || true
  pkill -f 'dnsmasq.*wcop'        2>/dev/null || true
  pkill -f 'airodump-ng.*wscan'   2>/dev/null || true

  # старый конфиг (новый лежит в /etc/wlab/wlab.conf)
  if [[ -f /etc/wlab.conf ]]; then
    mv -f /etc/wlab.conf /etc/wlab/wlab.conf.v9.bak
    warn "старый /etc/wlab.conf сохранён как /etc/wlab/wlab.conf.v9.bak"
  fi

  rm -f /tmp/wcop_hostapd.conf /tmp/wcop_dnsmasq.conf 2>/dev/null || true
  rm -f /tmp/wscan_live-* /tmp/ble_packets_* /tmp/ble_cache_* 2>/dev/null || true
  rm -f /tmp/wlab_scan_* 2>/dev/null || true

  ok "остатки v9 убраны"
  echo
fi

# --- предупредить, если старые команды всё ещё торчат в PATH ---------------
LEFT=""
for c in wcop Wscan Wdeaut Watak bscan bpak sscan wclog wtaak wpoisk; do
  p=$(command -v "$c" 2>/dev/null || true)
  [[ -n "$p" ]] && LEFT="$LEFT $c"
done
if [[ -n "$LEFT" ]]; then
  warn "в PATH остались команды старой версии:$LEFT"
  warn "убрать: sudo bash install.sh --clean-v9"
  echo
fi

# ---------------------------------------------------------------------------
if [[ $NO_DEPS -eq 0 ]]; then
  say "ставлю зависимости (это займёт пару минут)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || warn "apt-get update ругнулся, продолжаю"

  CORE="aircrack-ng mdk4 iw iproute2 python3 macchanger gawk grep sed coreutils"
  NET="whois dnsutils curl wget jq"
  WEB="nmap whatweb nikto wafw00f"
  AP="hostapd dnsmasq"
  BLE="tshark"
  PMKID="hcxdumptool hcxtools"

  for group in "$CORE" "$NET" "$WEB" "$AP" "$BLE" "$PMKID"; do
    # shellcheck disable=SC2086
    apt-get install -y -qq $group >/dev/null 2>&1 || warn "часть пакетов не встала: $group"
  done
  ok "зависимости"
else
  warn "зависимости пропущены (--no-deps)"
fi

# ---------------------------------------------------------------------------
say "копирую в $DEST"
install -d -m 0755 "$DEST"
install -d -m 0755 "$ETC"
install -d -m 0755 "$STATE"

rm -rf "$DEST/bin" "$DEST/lib" "$DEST/share" "$DEST/etc"
cp -r "$SRC/bin" "$SRC/lib" "$SRC/share" "$SRC/etc" "$DEST/"
install -m 0755 "$SRC/wlab" "$DEST/wlab"
chmod 0755 "$DEST"/bin/* 2>/dev/null || true
chmod 0644 "$DEST"/lib/*.py 2>/dev/null || true

ln -sfn "$DEST/wlab" "$BINLINK"
# sudo часто режет /usr/local/bin из secure_path -> дублируем в /usr/bin
ln -sfn "$DEST/wlab" /usr/bin/wlab
ok "команда: wlab"

# --- старые имена команд (wcop, Wdeaut, Watak, bscan ...) -------------------
if [[ -d "$SRC/compat" ]]; then
  for f in "$SRC/compat"/*; do
    [[ -f "$f" ]] || continue
    install -m 0755 "$f" /usr/local/bin/
  done
  ok "старые команды: wcop, wdeaut, wtaak, Watak, Wscan, wpoisk, bscan, bpak, sscan, wclog, stop"
fi

# ---------------------------------------------------------------------------
if [[ ! -f "$ETC/wlab.conf" ]]; then
  install -m 0644 "$SRC/etc/wlab.conf" "$ETC/wlab.conf"
  ok "конфиг: $ETC/wlab.conf"
else
  warn "конфиг уже есть, не трогаю: $ETC/wlab.conf"
fi

# конфиг должен читаться обычным юзером, иначе wlab version падает
chmod 0644 "$ETC/wlab.conf" 2>/dev/null || true

# ---------------------------------------------------------------------------
if [[ $NO_UDEV -eq 0 && -f "$SRC/share/99-wlab-nrf.rules" ]]; then
  install -m 0644 "$SRC/share/99-wlab-nrf.rules" /etc/udev/rules.d/99-wlab-nrf.rules
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger 2>/dev/null || true
  ok "udev-правило: /dev/nrf52"
fi

# ---------------------------------------------------------------------------
echo
ok "установлено. Проверка:"
echo "    wlab version"
echo "    wlab status"
echo
echo "  Дальше:"
echo "    1) подгони /etc/wlab/wlab.conf под свои адаптеры"
echo "    2) wlab scan"
echo "    3) wlab osint example.com"
echo
echo "  Шпаргалка: wlab help"
echo "  Автор: @w0rkil0m · t.me/w0rkil0m"
echo
