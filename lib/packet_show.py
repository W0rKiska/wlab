#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wlab pak — детальный разбор BLE-пакета из последней сессии.
Читает /var/log/wlab/ble.jsonl, который пишет wlab ble.
"""
import json
import os
import re
import sys

WLAB_STATE = os.environ.get("WLAB_STATE", "/var/log/wlab")
DEFAULT_FILE = os.path.join(WLAB_STATE, "ble.jsonl")

AD_TYPES = {
    0x01: "Flags",
    0x02: "UUID16 — incomplete",
    0x03: "UUID16 — complete",
    0x04: "UUID32 — incomplete",
    0x05: "UUID32 — complete",
    0x06: "UUID128 — incomplete",
    0x07: "UUID128 — complete",
    0x08: "Shortened Local Name",
    0x09: "Complete Local Name",
    0x0A: "TX Power Level",
    0x0D: "Class of Device",
    0x0F: "Service Solicitation — 16",
    0x10: "Service Data — 16",
    0x14: "Service Solicitation — 128",
    0x16: "Service Data — 16 (0x16)",
    0x1F: "Service Solicitation — 32",
    0x20: "Service Data — 32",
    0x21: "Service Data — 128",
    0xFF: "Manufacturer Specific",
}

# Bluetooth SIG Company Identifier (частично — самые частые в быту)
COMPANY = {
    0x0006: "Microsoft",
    0x004C: "Apple",
    0x0059: "Nordic Semiconductor",
    0x0075: "Samsung",
    0x0087: "Garmin",
    0x00E0: "Google",
    0x00D2: "Amazon",
    0x0157: "Anhui Huami (Amazfit)",
    0x0154: "Huawei",
    0x027D: "Huawei",
    0x038F: "Xiaomi",
    0x0499: "Ruuvi",
    0x0A5C: "Broadcom",
    0xFE95: "Xiaomi",
    0x0969: "Xiaomi",
    0xFEB7: "Facebook",
    0xFD6F: "Google (Exposure Notification)",
}

FLAGS = [
    (0x01, "LE Limited Discoverable"),
    (0x02, "LE General Discoverable"),
    (0x04, "BR/EDR not supported"),
    (0x08, "LE + BR/EDR Controller"),
    (0x10, "LE + BR/EDR Host"),
]


def oui(mac):
    pref = mac.lower()[:8]
    for db in ("/usr/share/wireshark/manuf", "/usr/share/nmap/nmap-mac-prefixes"):
        try:
            with open(db, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith(pref):
                        parts = line.split(None, 1)
                        if len(parts) == 2:
                            return parts[1].strip()
        except OSError:
            continue
    return ""


def addr_kind(mac):
    try:
        b = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return "?"
    if b >> 6 != 0b11:
        return "публичный (public)"
    sub = (b >> 6) & 0b11
    return {
        0b00: "случайный, non-resolvable",
        0b01: "случайный, resolvable (RPA)",
        0b10: "случайный, reserved",
        0b11: "случайный, static",
    }[sub]


def hexbytes(s):
    h = re.sub(r"[^0-9a-fA-F]", "", s or "")
    if len(h) % 2:
        h = h[:-1]
    return bytes.fromhex(h) if h else b""


def decode_ad(raw):
    data = hexbytes(raw)
    out, i = [], 0
    while i < len(data):
        ln = data[i]
        if ln == 0:
            i += 1
            continue
        if i + 1 + ln > len(data):
            out.append(("truncated", data[i:]))
            break
        adt = data[i + 1]
        out.append((adt, data[i + 2:i + 1 + ln]))
        i += 1 + ln
    return out


def fmt_payload(adt, p):
    if adt == 0x01 and p:
        return ", ".join(name for bit, name in FLAGS if p[0] & bit) or "нет флагов"
    if adt in (0x08, 0x09):
        return p.decode("utf-8", "replace")
    if adt == 0x0A and p:
        v = p[0] - 256 if p[0] > 127 else p[0]
        return f"{v} dBm"
    if adt in (0x02, 0x03) and len(p) >= 2:
        uuids = [f"0x{p[j + 1]:02x}{p[j]:02x}" for j in range(0, len(p) - 1, 2)]
        return ", ".join(uuids)
    if adt == 0xFF and len(p) >= 2:
        cid = int.from_bytes(p[:2], "little")
        name = COMPANY.get(cid, "неизвестная компания")
        return f"company 0x{cid:04X} ({name}) · data {p[2:].hex(' ')}"
    if adt in (0x16, 0x10) and len(p) >= 2:
        uid = f"0x{p[1]:02x}{p[0]:02x}"
        return f"uuid {uid} · data {p[2:].hex(' ')}"
    return p.hex(" ")


def load(path):
    recs = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def main():
    args = [a for a in sys.argv[1:] if a]
    path = DEFAULT_FILE
    if "--file" in args:
        j = args.index("--file")
        path = args[j + 1]
        args = args[:j] + args[j + 2:]

    if not os.path.isfile(path):
        print(f"[-] нет файла сессии: {path}")
        print("    сначала запусти: wlab ble")
        sys.exit(1)

    recs = load(path)

    if args and args[0] in ("-l", "--list"):
        print(f"[*] {path} — {len(recs)} пакетов, последние 20:")
        for r in recs[-20:]:
            print(f"  #{r.get('frame','?'):>6}  {r.get('t','')[:10]:>10}  "
                  f"{r.get('src','?'):<17}  {r.get('pdu','?'):<11}  {r.get('name','-')}")
        return

    if not args:
        print("Использование: wlab pak <номер> [--file путь]")
        print("               wlab pak --list")
        sys.exit(1)

    if not args[0].isdigit():
        print(f"[-] номер пакета должен быть числом: {args[0]}")
        sys.exit(1)

    want = args[0]
    rec = next((r for r in recs if str(r.get("frame")) == want), None)
    if rec is None:
        print(f"[-] пакет #{want} не найден")
        frames = [r.get("frame") for r in recs if r.get("frame")]
        if frames:
            print(f"    есть: {frames[0]}..{frames[-1]} ({len(frames)} шт)")
        sys.exit(1)

    src, dst = rec.get("src", "?"), rec.get("dst", "?")
    print("=" * 70)
    print(f"  ПАКЕТ #{rec.get('frame')}   {rec.get('pdu')}   rssi={rec.get('rssi')} dBm")
    print("=" * 70)
    print(f"  Время:      +{rec.get('t')} s от старта")
    print(f"  Откуда:     {src}  {oui(src)}")
    print(f"              тип адреса: {addr_kind(src)}")
    print(f"  Куда:       {dst}")
    if dst != "?" and dst != "BCAST":
        print(f"              {oui(dst)} · тип адреса: {addr_kind(dst)}")
    name = rec.get("name", "-")
    if name and name != "-":
        print(f"  Имя:        {name}")
    if rec.get("mfr"):
        print(f"  Mfr data:   {rec['mfr']}")

    ad = rec.get("ad", "")
    parsed = decode_ad(ad)
    if parsed:
        print("-" * 70)
        print("  AD-СТРУКТУРА:")
        for i, (adt, payload) in enumerate(parsed, 1):
            label = AD_TYPES.get(adt, f"неизвестный 0x{adt:02X}") if isinstance(adt, int) else adt
            print(f"   {i}. [{label}]")
            print(f"      {fmt_payload(adt, payload)}")
    elif ad:
        print("-" * 70)
        print(f"  RAW: {ad}")

    print("=" * 70)


if __name__ == "__main__":
    main()
