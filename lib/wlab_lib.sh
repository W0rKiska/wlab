#!/usr/bin/env bash
# ============================================================================
#  WLAB :: общая библиотека
#  Подключается всеми скриптами: source "$WLAB_ROOT/lib/wlab_lib.sh"
# ============================================================================
[[ -n "${_WLAB_LIB:-}" ]] && return 0
_WLAB_LIB=1

WLAB_VERSION="10.0"

WLAB_ROOT="${WLAB_ROOT:-/opt/wlab}"
WLAB_ETC="${WLAB_ETC:-/etc/wlab}"
WLAB_STATE="${WLAB_STATE:-/var/log/wlab}"
WLAB_RUN="${WLAB_RUN:-/run/wlab}"
WLAB_CONF="${WLAB_CONF:-${WLAB_ETC}/wlab.conf}"

umask 022

# ---------------------------------------------------------------------------
#  ЦВЕТА (только когда stdout — терминал; NO_COLOR отключает)
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_R=$'\033[0m'
  C_B=$'\033[1m'
  C_RED=$'\033[31m'
  C_GRN=$'\033[32m'
  C_YEL=$'\033[33m'
  C_CYN=$'\033[36m'
  C_DIM=$'\033[90m'
else
  C_R=''; C_B=''; C_RED=''; C_GRN=''; C_YEL=''; C_CYN=''; C_DIM=''
fi
export C_R C_B C_RED C_GRN C_YEL C_CYN C_DIM

# ---------------------------------------------------------------------------
#  ВЫВОД
# ---------------------------------------------------------------------------
wlab_info() { printf '%s[*]%s %s\n' "$C_CYN" "$C_R" "$*" >&2; }
wlab_ok()   { printf '%s[+]%s %s\n' "$C_GRN" "$C_R" "$*" >&2; }
wlab_warn() { printf '%s[!]%s %s\n' "$C_YEL" "$C_R" "$*" >&2; }
wlab_err()  { printf '%s[-]%s %s\n' "$C_RED" "$C_R" "$*" >&2; }
wlab_die()  { wlab_err "$*"; exit 1; }
wlab_dbg()  { if [[ -n "${WLAB_DEBUG:-}" ]]; then printf '%s[dbg]%s %s\n' "$C_DIM" "$C_R" "$*" >&2; fi; return 0; }

wlab_hr() { printf '%s%s%s\n' "$C_DIM" "────────────────────────────────────────────────────────────" "$C_R" >&2; }

wlab_banner() {
  local t="${1:0:56}"
  printf '\n%s╔══════════════════════════════════════════════════════════╗%s\n' "$C_CYN" "$C_R" >&2
  printf '%s║%s %-56s %s║%s\n' "$C_CYN" "$C_R" "$t" "$C_CYN" "$C_R" >&2
  printf '%s╚══════════════════════════════════════════════════════════╝%s\n' "$C_CYN" "$C_R" >&2
}

# Большое лого в терминале (ANSI Shadow), с градиентом зелёный -> циан
wlab_logo() {
  local a b c d e f
  if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    a=$'\033[38;2;0;255;156m'
    b=$'\033[38;2;0;240;170m'
    c=$'\033[38;2;0;225;184m'
    d=$'\033[38;2;12;210;198m'
    e=$'\033[38;2;28;205;214m'
    f=$'\033[38;2;34;211;238m'
  else
    a=''; b=''; c=''; d=''; e=''; f=''
  fi
  printf '%s\n' \
"${a}  ██╗    ██╗██╗      █████╗ ██████╗ ${C_R}" \
"${b}  ██║    ██║██║     ██╔══██╗██╔══██╗${C_R}" \
"${c}  ██║ █╗ ██║██║     ███████║██████╔╝${C_R}" \
"${d}  ██║███╗██║██║     ██╔══██║██╔══██╗${C_R}" \
"${e}  ╚███╔███╔╝███████╗██║  ██║██████╔╝${C_R}" \
"${f}   ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝╚═════╝ ${C_R}"
  printf '  %sWi-Fi · BLE · OSINT%s        %sv%s%s\n' \
    "$C_DIM" "$C_R" "$C_DIM" "$WLAB_VERSION" "$C_R"
  printf '  %s@w0rkil0m%s %s· t.me/w0rkil0m%s\n\n' \
    "$C_GRN" "$C_R" "$C_DIM" "$C_R"
}

# ---------------------------------------------------------------------------
#  КОНФИГ
# ---------------------------------------------------------------------------
wlab_load_conf() {
  if [[ -f "$WLAB_CONF" ]]; then
    if [[ -r "$WLAB_CONF" ]]; then
      # shellcheck disable=SC1090
      source "$WLAB_CONF"
    else
      printf '%s[!]%s нет доступа к %s — беру дефолты. Чини: sudo chmod 644 %s\n' \
        "$C_YEL" "$C_R" "$WLAB_CONF" "$WLAB_CONF" >&2
    fi
  fi
  : "${IFACE_MON:=wlan1}"
  : "${IFACE_AP:=wlan0}"
  : "${IFACE_SCAN:=${IFACE_AP}}"
  : "${IFACE_BLE:=/dev/ttyACM0}"
  : "${IFACE_UP:=}"
  : "${AP_IP:=192.168.50.1}"
  : "${AP_PREFIX:=24}"
  : "${DHCP_RANGE:=192.168.50.10,192.168.50.100}"
  : "${AP_SSID:=FreeWiFi}"
  : "${AP_PASS:=}"
  : "${CHANNEL_DEFAULT:=6}"
  : "${ENABLE_NAT:=1}"
  : "${ANON_MAC:=1}"
  : "${PORTAL_DIR:=${WLAB_ROOT}/share/portal}"
  : "${LOG_JSONL:=1}"
  : "${SCAN_LIMIT:=15}"
  export IFACE_MON IFACE_AP IFACE_SCAN IFACE_BLE IFACE_UP AP_IP AP_PREFIX DHCP_RANGE
  export AP_SSID AP_PASS CHANNEL_DEFAULT ENABLE_NAT ANON_MAC PORTAL_DIR LOG_JSONL SCAN_LIMIT
}

# ---------------------------------------------------------------------------
#  КАТАЛОГИ
# ---------------------------------------------------------------------------
wlab_dirs() {
  mkdir -p "$WLAB_STATE" "$WLAB_RUN" 2>/dev/null || true
  mkdir -p "$WLAB_STATE/ap" "$WLAB_STATE/scan" "$WLAB_STATE/ble" \
           "$WLAB_STATE/osint" "$WLAB_STATE/caps" 2>/dev/null || true
  chmod 755 "$WLAB_STATE" "$WLAB_STATE"/* 2>/dev/null || true
  chmod 700 "$WLAB_RUN" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
#  ROOT
#  Использование в скрипте:  wlab_need_root "$SELF" "$@"
# ---------------------------------------------------------------------------
wlab_need_root() {
  local self=$1; shift
  [[ ${EUID} -eq 0 ]] && return 0
  wlab_warn "нужен root — перезапускаюсь через sudo"
  exec sudo "$self" "$@"
}

# ---------------------------------------------------------------------------
#  JSONL-ЛОГИ
#  Один факт = одна строка. Грепается, парсится jq, не ломает таблицы.
# ---------------------------------------------------------------------------
wlab_json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/ }
  s=${s//$'\r'/ }
  s=${s//$'\t'/ }
  printf '%s' "$s"
}

wlab_log() {  # wlab_log <tool> <evt> [k=v ...]
  [[ "${LOG_JSONL:-1}" == "1" ]] || return 0
  local tool=$1 evt=$2; shift 2
  local ts kv k v line
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  line="{\"ts\":\"$ts\",\"tool\":\"$tool\",\"evt\":\"$evt\""
  for kv in "$@"; do
    k=${kv%%=*}
    v=${kv#*=}
    line="${line},\"$(wlab_json_escape "$k")\":\"$(wlab_json_escape "$v")\""
  done
  line="${line}}"
  printf '%s\n' "$line" >> "${WLAB_STATE}/${tool}.jsonl" 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
#  ИНТЕРФЕЙСЫ
# ---------------------------------------------------------------------------
wlab_iface_exists() { ip link show "$1" >/dev/null 2>&1; }

# NetworkManager любит дёргать адаптер и ронять его посреди скана
# (симптом: airodump -> "read failed: Network is down").
# На время работы отдаём интерфейс себе, потом возвращаем.
wlab_nm_unmanage() {
  local ifc=${1:?}
  command -v nmcli >/dev/null 2>&1 || return 0
  # Сначала рвём активное соединение: пока оно живо, "managed no" не сработает,
  # а NM будет держать адаптер и ронять его в мониторе.
  nmcli device disconnect "$ifc" >/dev/null 2>&1 || true
  nmcli device set "$ifc" managed no >/dev/null 2>&1 || true
  return 0
}

wlab_nm_manage() {
  local ifc=${1:?}
  command -v nmcli >/dev/null 2>&1 || return 0
  nmcli device set "$ifc" managed yes >/dev/null 2>&1 || true
  return 0
}

wlab_iface_type() {
  # awk выходит на первом совпадении -> iw ловит SIGPIPE. Под pipefail это 141.
  iw dev "$1" info 2>/dev/null | awk '/type/ {print $2; exit}' || true
}

wlab_mon_on() {
  local ifc=${1:?}
  wlab_iface_exists "$ifc" || wlab_die "интерфейс '$ifc' не найден (ip link show)"
  wlab_nm_unmanage "$ifc"
  ip link set "$ifc" down 2>/dev/null || wlab_die "не могу опустить $ifc"
  if ! iw dev "$ifc" set type monitor 2>/dev/null; then
    ip link set "$ifc" up 2>/dev/null || true
    wlab_nm_manage "$ifc"
    wlab_die "$ifc не умеет monitor mode (проверь: iw list | grep -A8 'Supported interface modes')"
  fi
  ip link set "$ifc" up 2>/dev/null || wlab_die "не могу поднять $ifc"
  # висящий IP от NetworkManager мешает некоторым драйверам ловить beacon
  ip addr flush dev "$ifc" 2>/dev/null || true
  sleep 0.2
  if [[ "$(wlab_iface_type "$ifc")" != "monitor" ]]; then
    wlab_warn "$ifc не остался в monitor — NetworkManager отбирает. Попробуй --kill"
  fi
  wlab_log iface mon_on "iface=$ifc"
}

wlab_mon_off() {
  local ifc=${1:-$IFACE_MON}
  ip link set "$ifc" down 2>/dev/null || true
  iw dev "$ifc" set type managed 2>/dev/null || true
  ip link set "$ifc" up 2>/dev/null || true
  wlab_nm_manage "$ifc"
  wlab_log iface mon_off "iface=$ifc"
}

wlab_set_channel() {
  local ifc=${1:?} ch=${2:?}
  [[ "$ch" =~ ^[0-9]+$ ]] || wlab_die "канал должен быть числом, а не '$ch'"
  iw dev "$ifc" set channel "$ch" 2>/dev/null \
    || wlab_die "не могу поставить канал $ch на $ifc (регуляторный домен? iw reg set RU)"
}

wlab_restore_nm() {
  systemctl is-enabled NetworkManager >/dev/null 2>&1 || return 0
  systemctl start NetworkManager >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
#  АНОНИМНОСТЬ
# ---------------------------------------------------------------------------
wlab_random_mac() {
  local ifc=${1:?} mac
  ip link set "$ifc" down 2>/dev/null || true
  if command -v macchanger >/dev/null 2>&1; then
    macchanger -r "$ifc" >/dev/null 2>&1 || true
  else
    mac=$(printf '02:%02x:%02x:%02x:%02x:%02x' \
          $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) \
          $((RANDOM % 256)) $((RANDOM % 256)))
    ip link set "$ifc" address "$mac" 2>/dev/null || true
  fi
  ip link set "$ifc" up 2>/dev/null || true
  mac=$(cat "/sys/class/net/${ifc}/address" 2>/dev/null || echo '?')
  wlab_log iface mac_random "iface=$ifc" "mac=$mac"
  printf '%s' "$mac"
}

# ---------------------------------------------------------------------------
#  ПРОЧЕЕ
# ---------------------------------------------------------------------------
wlab_require_cmds() {
  local c missing=()
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  if (( ${#missing[@]} > 0 )); then
    wlab_err "не хватает: ${missing[*]}"
    wlab_info "ставь: sudo apt install -y ${missing[*]}"
    return 1
  fi
  return 0
}

wlab_confirm() {
  local ans
  printf '%s[?]%s %s [y/N]: ' "$C_YEL" "$C_R" "$1" >&2
  read -r ans < /dev/tty 2>/dev/null || return 1
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

wlab_strip_ansi() { sed -r 's/\x1B\[[0-9;]*[A-Za-z]//g'; }

wlab_pidfile() { printf '%s/%s.pid' "$WLAB_RUN" "$1"; }

wlab_kill_pid() {
  local name=$1 f pid
  f=$(wlab_pidfile "$name")
  [[ -f "$f" ]] || return 0
  pid=$(cat "$f" 2>/dev/null || true)
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  rm -f "$f"
  return 0
}

wlab_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

# Найти канал точки по BSSID: короткий скан по всем каналам
wlab_find_channel() {   # wlab_find_channel <bssid> <iface>
  local bssid=${1:?} ifc=${2:?} tmp csv ch
  tmp=$(mktemp -d 2>/dev/null) || return 1
  timeout 15 airodump-ng -w "$tmp/s" --output-format csv "$ifc" >/dev/null 2>&1 || true
  csv=$(ls "$tmp"/s-*.csv 2>/dev/null | head -1 || true)
  if [[ -n "$csv" ]]; then
    ch=$(awk -F, -v b="${bssid,,}" \
         'tolower($1) ~ b { gsub(/[ \t\r]/,"",$4); if ($4 ~ /^[0-9]+$/) { print $4; exit } }' \
         "$csv" 2>/dev/null | head -1 || true)
  fi
  rm -rf "$tmp" 2>/dev/null || true
  [[ -n "${ch:-}" ]] || return 1
  printf '%s' "$ch"
}

# OUI производитель по MAC (wireshark manuf -> nmap prefixes)
wlab_oui() {
  local pref db
  pref=$(printf '%s' "$1" | cut -c1-8 | tr 'A-Z' 'a-z')
  for db in /usr/share/wireshark/manuf /usr/share/nmap/nmap-mac-prefixes; do
    [[ -r "$db" ]] || continue
    awk -v p="$pref" 'BEGIN{IGNORECASE=1} $0 ~ "^"p { $1=""; sub(/^[ \t]+/,""); print; exit }' "$db" || true
    return 0
  done
  return 0
}
