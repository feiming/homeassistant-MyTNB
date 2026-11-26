"""Test script: download the latest TNB bill PDF(s).

Flow (mirrors what the browser does):
  1. Log in (sitecore Login -> SSO handler), same as get_smartmeter_data.py.
  2. Load /AccountManagement/IndividualDashboard.
  3. Find every "View Bill" button: onclick="PrintPDF('<hex>')".
     The hex is a UTF-16LE encoded, encrypted account token.
  4. POST "<hex>,True" to ViewAccountProfile/InitProtectID, which returns
     the encrypted billingNo (this is what the page's PrintPDF() JS does).
  5. Download PrintPDF2?billingNo=..., extract the NEM balance, account
     summary and meter readings in memory, then discard the PDF
     (pass --keep to save it to bills/).

If you already have a PrintPDF2 link (from the browser's network tab), pass
it with --pdf-url to test just the download step with a fresh session.

Usage:
  python get_bill.py                 # all accounts on the dashboard
  python get_bill.py --keep          # also save the PDF to bills/
  python get_bill.py --pdf-url URL   # download a known PrintPDF2 link
  python get_bill.py --debug         # verbose, saves responses for inspection
  python get_bill.py --pdf FILE      # just extract bill info from a saved bill

Needs USERNAME and PASSWORD in the environment or a .env file.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests
from dotenv import load_dotenv
from pypdf import PdfReader

# override=True so .env wins over a USERNAME already set in the shell.
load_dotenv(override=True)

BASE = "https://myaccount.mytnb.com.my"
LOGIN_URL = "https://www.mytnb.com.my/api/sitecore/Account/Login"
SSO_URL = f"{BASE}/SSO/SSOHandler"
DASHBOARD_URL = f"{BASE}/AccountManagement/IndividualDashboard"
INIT_PROTECT_URL = f"{BASE}/AccountManagement/ViewAccountProfile/InitProtectID"
PDF_URL = f"{BASE}/AccountManagement/ViewAccountProfile/PrintPDF2"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

OUT_DIR = Path("bills")
DEBUG_DIR = OUT_DIR / "debug"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
DEBUG = False


def log(*args):
    if DEBUG:
        print("[debug]", *args)


def dump(name, content):
    """Save a debug artefact (HTML/JS) for manual inspection."""
    if not DEBUG:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / name
    path.write_text(content, encoding="utf-8")
    log(f"saved {path}")


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def login(username, password):
    resp = SESSION.post(LOGIN_URL, data={"Email": username, "Password": password})
    resp.raise_for_status()
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', resp.text))
    if not fields:
        sys.exit("Login failed: no SSO form fields returned (bad credentials?)")

    resp = SESSION.post(SSO_URL, data=fields)
    if resp.status_code != 200:
        sys.exit(f"SSO handler rejected login: HTTP {resp.status_code}")
    print("Logged in.")


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def get_dashboard():
    resp = SESSION.get(DASHBOARD_URL, headers={"Referer": SSO_URL})
    dump("dashboard.html", resp.text)
    if "SessionExpired" in resp.url or resp.status_code != 200:
        sys.exit(f"Dashboard not reachable (HTTP {resp.status_code}, url={resp.url})")
    return resp.text


def decode_token(hex_token):
    """PrintPDF's argument is a UTF-16LE string encoded as hex."""
    try:
        return bytes.fromhex(hex_token).decode("utf-16-le")
    except ValueError:
        return None


def find_bill_tokens(html):
    tokens = re.findall(r"PrintPDF\(\s*'([0-9A-Fa-f]+)'\s*\)", html)
    # Keep order, drop duplicates.
    return list(dict.fromkeys(tokens))


# --------------------------------------------------------------------------- #
# Resolve token -> PrintPDF2 URL
# --------------------------------------------------------------------------- #
def resolve_pdf_url(hex_token, owned=True):
    """Replay the dashboard's PrintPDF(billingNo) JS:

        $.ajax({type: "POST",
                url: "/AccountManagement/ViewAccountProfile/InitProtectID",
                data: {selectedAccountID: billingNo + "," + IsOwnedAccount},
                success: r => window.open("/.../PrintPDF2?billingNo=" + r)})

    The returned token is inserted into the URL as-is (not URL-encoded).
    """
    resp = SESSION.post(
        INIT_PROTECT_URL,
        data={"selectedAccountID": f"{hex_token},{owned}"},
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": DASHBOARD_URL},
    )
    dump("initprotectid_response.txt", resp.text)
    if resp.status_code != 200:
        print(f"  ✗ InitProtectID failed: HTTP {resp.status_code}")
        return None
    billing_no = resp.text.strip().strip('"')
    log(f"billingNo={billing_no[:40]}… ({len(billing_no)} chars)")
    return f"{PDF_URL}?billingNo={billing_no}"


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_pdf(url_or_response, name_hint, keep=False):
    if isinstance(url_or_response, requests.Response):
        resp = url_or_response
    else:
        log(f"GET {url_or_response}")
        resp = SESSION.get(url_or_response, headers={"Referer": DASHBOARD_URL})

    if resp.content[:4] != b"%PDF":
        ctype = resp.headers.get("Content-Type")
        dump(f"not_pdf_{name_hint}.html", resp.text)
        print(f"  ✗ Not a PDF (HTTP {resp.status_code}, {ctype}, final url {resp.url})")
        return None

    print(f"  ✓ Downloaded bill ({len(resp.content):,} bytes)")
    info = report_bill(resp.content)

    if not keep:
        # Data extracted from memory; nothing is written to disk.
        return info

    OUT_DIR.mkdir(exist_ok=True)
    filename = name_hint
    cd = resp.headers.get("Content-Disposition", "")
    if m := re.search(r'filename="?([^";]+)"?', cd):
        filename = Path(m.group(1)).stem
    path = OUT_DIR / f"{filename}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    path.write_bytes(resp.content)
    print(f"  ✓ Saved {path}")
    return info


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
# Page 1 summary box reads (one item per line):
#   Baki NEM / 970kWh / Tarikh Luput / Baki NEM / 31.12.2026
#   Baki Terdahulu -RM262.77 / Caj Semasa NEM RM2.74 / Pelarasan Penggenapan RM0.01
#   Baki NEM Terkumpul RM0.00 / Baki NEM Dibawa Ke Hadapan / - Luput pada 31.12.2026 / RM0.00
_RM = r"([-−]?\s*RM\s*[-−]?[\d,]+\.\d{2})"
_NEM_BALANCE_RE = re.compile(r"Baki NEM\s*\n\s*([\d,]+(?:\.\d+)?)\s*kWh")
_NEM_EXPIRY_RE = re.compile(r"Luput pada\s*(\d{2}\.\d{2}\.\d{4})")
_NEM_EXPIRY_FALLBACK_RE = re.compile(r"Tarikh Luput[\s\S]{0,200}?(\d{2}\.\d{2}\.\d{4})")
_SUMMARY_RES = {
    "baki_terdahulu": re.compile(r"Baki Terdahulu\s*" + _RM),
    "caj_semasa_nem": re.compile(r"Caj Semasa NEM\s*" + _RM),
    "pelarasan_penggenapan": re.compile(r"Pelarasan Penggenapan\s*" + _RM),
    "baki_nem_terkumpul": re.compile(r"Baki NEM Terkumpul\s*" + _RM),
    # amount sits after the "- Luput pada dd.mm.yyyy" line
    "baki_nem_dibawa_ke_hadapan": re.compile(r"Baki NEM Dibawa Ke Hadapan[\s\S]{0,80}?" + _RM),
}
# Page 3 "Maklumat Meter": M <meter no> <dahulu> <semasa> <penggunaan> <unit> [(E)|(I)]
_METER_RE = re.compile(
    r"^M\s+(\S+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(kWh|kW|kVARh)\b\s*(?:\((\w)\))?",
    re.M,
)
_METER_DIRECTION = {"E": "export", "I": "import"}


def _rm(value):
    """'-RM262.77' / '−262.02' / 'RM0.01' -> float."""
    neg = "-" in value or "−" in value
    num = float(re.sub(r"[^\d.]", "", value))
    return -num if neg else num


def _int(value):
    return int(value.replace(",", ""))


def extract_bill(pdf_bytes):
    pages = [page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages]
    for i, text in enumerate(pages, 1):
        dump(f"bill_page{i}.txt", text)
    page1 = pages[0]
    alltext = "\n".join(pages)

    info = {"baki_nem_kwh": None, "tarikh_luput": None}
    if m := _NEM_BALANCE_RE.search(page1):
        info["baki_nem_kwh"] = float(m.group(1).replace(",", ""))
    if m := (_NEM_EXPIRY_RE.search(page1) or _NEM_EXPIRY_FALLBACK_RE.search(page1)):
        info["tarikh_luput"] = datetime.strptime(m.group(1), "%d.%m.%Y").date()

    for key, pattern in _SUMMARY_RES.items():
        m = pattern.search(page1)
        info[key] = _rm(m.group(1)) if m else None

    info["meters"] = [
        {
            "meter_no": m.group(1),
            "dahulu": _int(m.group(2)),
            "semasa": _int(m.group(3)),
            "penggunaan": _int(m.group(4)),
            "unit": m.group(5),
            "direction": _METER_DIRECTION.get(m.group(6)),
        }
        for m in _METER_RE.finditer(alltext)
    ]
    return info


def report_bill(pdf_bytes):
    info = extract_bill(pdf_bytes)

    def fmt_rm(v):
        if v is None:
            return "not found"
        return f"{'-' if v < 0 else ''}RM{abs(v):,.2f}"

    kwh = info["baki_nem_kwh"]
    print(f"  Baki NEM:                   {'not found' if kwh is None else f'{kwh:g} kWh'}")
    print(f"  Tarikh Luput:               {info['tarikh_luput'] or 'not found'}")
    print(f"  Baki Terdahulu:             {fmt_rm(info['baki_terdahulu'])}")
    print(f"  Caj Semasa NEM:             {fmt_rm(info['caj_semasa_nem'])}")
    print(f"  Pelarasan Penggenapan:      {fmt_rm(info['pelarasan_penggenapan'])}")
    print(f"  Baki NEM Terkumpul:         {fmt_rm(info['baki_nem_terkumpul'])}")
    print(f"  Baki NEM Dibawa Ke Hadapan: {fmt_rm(info['baki_nem_dibawa_ke_hadapan'])}")
    print("  Meter readings:")
    if not info["meters"]:
        print("    not found")
    for m in info["meters"]:
        direction = f" ({m['direction']})" if m["direction"] else ""
        print(
            f"    {m['meter_no']}  {m['dahulu']:>8,} → {m['semasa']:>8,}  "
            f"= {m['penggunaan']:>6,} {m['unit']}{direction}"
        )
    return info


def main():
    global DEBUG
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf-url", help="Known PrintPDF2?billingNo=... URL to download")
    parser.add_argument("--debug", action="store_true", help="Verbose output, save responses to bills/debug/")
    parser.add_argument("--keep", action="store_true", help="Also save the downloaded PDF to bills/")
    parser.add_argument("--pdf", type=Path, help="Saved bill PDF to parse (skips login/download)")
    args = parser.parse_args()
    DEBUG = args.debug

    if args.pdf:
        print(args.pdf)
        report_bill(args.pdf.read_bytes())
        return

    login(os.environ["USERNAME"], os.environ["PASSWORD"])
    html = get_dashboard()

    if args.pdf_url:
        download_pdf(args.pdf_url, "bill", keep=args.keep)
        return

    tokens = find_bill_tokens(html)
    print(f"Found {len(tokens)} 'View Bill' button(s).")
    if not tokens:
        print("Run with --debug and check bills/debug/dashboard.html.")
        return

    for i, token in enumerate(tokens, 1):
        print(f"[{i}] account token {decode_token(token)}")
        url = resolve_pdf_url(token)
        if url:
            download_pdf(url, f"bill_{i}", keep=args.keep)

if __name__ == "__main__":
    main()
