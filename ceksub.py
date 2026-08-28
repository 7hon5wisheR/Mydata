
import requests
import re
import time

domain = input("Masukkan domain: ").strip().lower()

# Hapus http://, https://, dan / jika user memasukkannya
domain = re.sub(r"^https?://", "", domain)
domain = domain.split("/")[0]

subdomains = set()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120 Safari/537.36"
}


def add_subdomain(name):
    """Tambahkan subdomain jika masih bagian dari domain target."""
    name = name.strip().lower()

    if name.startswith("*."):
        name = name[2:]

    # Hilangkan titik di akhir
    name = name.rstrip(".")

    if name == domain or name.endswith("." + domain):
        subdomains.add(name)


# =========================================================
# 1. CRT.SH
# =========================================================

def get_crtsh():
    print("\n[+] Mencoba crt.sh...")

    url = f"https://crt.sh/?q=%25.{domain}&output=json"

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(f"    Status: {response.status_code}")

        if response.status_code != 200:
            print("    [-] crt.sh sedang tidak bisa digunakan.")
            return

        try:
            data = response.json()
        except ValueError:
            print("    [-] Respons crt.sh bukan JSON.")
            return

        for item in data:
            names = item.get("name_value", "")

            for name in names.split("\n"):
                add_subdomain(name)

        print("    [+] Selesai.")

    except requests.exceptions.Timeout:
        print("    [-] Timeout.")

    except requests.exceptions.RequestException as e:
        print(f"    [-] Error: {e}")


# =========================================================
# 2. HACKERTARGET
# =========================================================

def get_hackertarget():
    print("\n[+] Mencoba HackerTarget...")

    url = f"https://api.hackertarget.com/hostsearch/?q={domain}"

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(f"    Status: {response.status_code}")

        if response.status_code != 200:
            print("    [-] HackerTarget gagal.")
            return

        text = response.text

        if "error" in text.lower():
            print("    [-] HackerTarget memberikan error.")
            return

        for line in text.splitlines():
            # Format:
            # subdomain.domain.com,IP
            parts = line.split(",")

            if parts:
                add_subdomain(parts[0])

        print("    [+] Selesai.")

    except requests.exceptions.Timeout:
        print("    [-] Timeout.")

    except requests.exceptions.RequestException as e:
        print(f"    [-] Error: {e}")


# =========================================================
# 3. ALIENVAULT OTX
# =========================================================

def get_otx():
    print("\n[+] Mencoba AlienVault OTX...")

    url = (
        f"https://otx.alienvault.com/api/v1/indicators/domain/"
        f"{domain}/passive_dns"
    )

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        print(f"    Status: {response.status_code}")

        if response.status_code != 200:
            print("    [-] OTX gagal.")
            return

        try:
            data = response.json()
        except ValueError:
            print("    [-] Respons OTX bukan JSON.")
            return

        for item in data.get("passive_dns", []):
            hostname = item.get("hostname", "")
            add_subdomain(hostname)

        print("    [+] Selesai.")

    except requests.exceptions.Timeout:
        print("    [-] Timeout.")

    except requests.exceptions.RequestException as e:
        print(f"    [-] Error: {e}")


# =========================================================
# JALANKAN SEMUA SUMBER
# =========================================================

print("\n========================================")
print("       SUBDOMAIN ENUMERATOR")
print("========================================")
print(f"Target : {domain}")

get_crtsh()

time.sleep(1)

get_hackertarget()

time.sleep(1)

get_otx()


# =========================================================
# HASIL
# =========================================================

print("\n\n========================================")
print("           HASIL SUBDOMAIN")
print("========================================")

if not subdomains:
    print("Tidak ada subdomain yang ditemukan.")
else:
    for subdomain in sorted(subdomains):
        print(subdomain)

print("----------------------------------------")
print(f"Total: {len(subdomains)}")


# =========================================================
# SIMPAN KE FILE
# =========================================================

filename = f"subdomains_{domain}.txt"

try:
    with open(filename, "w", encoding="utf-8") as f:
        for subdomain in sorted(subdomains):
            f.write(subdomain + "\n")

    print(f"\n[+] Hasil disimpan ke: {filename}")

except Exception as e:
    print(f"\n[-] Gagal menyimpan file: {e}")

