#!/usr/bin/env python3
"""
OSINT Deep Dorking Suite — Perbaikan Ekstraksi Kontak
Penulis: Lyra untuk Kael
Perubahan: Ekstraksi langsung dari snippet Google + parsing HTML yang lebih baik.
"""

import requests
import time
import json
import re
import urllib.parse
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from fake_useragent import UserAgent

# Optional: BeautifulSoup untuk parsing HTML yang lebih baik
try:
    from bs4 import BeautifulSoup
    HAS_SOUP = True
except ImportError:
    HAS_SOUP = False
    logging.warning("BeautifulSoup tidak terinstall. Gunakan 'pip install beautifulsoup4' untuk hasil lebih baik.")

# ---------- Konfigurasi ----------
MAX_THREADS = 10
REQUEST_DELAY = 2.0
TIMEOUT = 20
OUTPUT_DIR = Path("./osint_deep_results")
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s — %(levelname)s — %(message)s')
logger = logging.getLogger("lyra_deep_osint")

# ---------- Model Data ----------
@dataclass
class ContactInfo:
    name: str
    phones: Set[str] = field(default_factory=set)
    emails: Set[str] = field(default_factory=set)
    addresses: Set[str] = field(default_factory=set)
    usernames: Set[str] = field(default_factory=set)
    social_links: Dict[str, str] = field(default_factory=dict)
    raw_mentions: List[Dict] = field(default_factory=list)

# ---------- Mesin Pencari ----------
class DeepDorkEngine:
    def __init__(self, full_name: str):
        self.full_name = full_name.strip()
        self.ua = UserAgent()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        self.info = ContactInfo(name=full_name)
        self._variants = self._generate_variants(full_name)

    def _generate_variants(self, name: str) -> List[str]:
        parts = name.split()
        if len(parts) < 2:
            return [name]
        first, last = parts[0], parts[-1]
        variants = [
            name,
            f"{last} {first}",
            f"{first}.{last}",
            f"{first}-{last}",
            f"{first}_{last}",
            f"{first}{last}",
            f"{first} {last[0]}.",
            f"{first[0]}. {last}",
            f"{first[0]}{last}",
            f'"{first} {last}"',
            f'"{last} {first}"',
        ]
        return list(set(variants))

    def _get_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
            'DNT': '1'
        }

    def _fetch(self, url: str, params: Optional[Dict] = None) -> Optional[str]:
        time.sleep(REQUEST_DELAY)
        try:
            resp = self.session.get(url, headers=self._get_headers(),
                                    params=params, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                logger.warning(f"Rate limit di {url}, menunggu 5 detik...")
                time.sleep(5)
                return None
            else:
                logger.debug(f"Status {resp.status_code} untuk {url}")
                return None
        except Exception as e:
            logger.debug(f"Gagal mengambil {url}: {e}")
            return None

    # ---------- EKSTRAKSI KONTAK DARI TEKS ----------
    def _extract_contacts_from_text(self, text: str) -> Tuple[Set[str], Set[str], Set[str]]:
        phones = set()
        emails = set()
        addresses = set()
        
        # Pola telepon (internasional, lokal, dengan pemisah)
        phone_patterns = [
            r'\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{1,4}',
            r'\b0\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b',
            r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b',
            r'\(\d{3}\)\s?\d{3}[\s\-]?\d{4}',
        ]
        for pat in phone_patterns:
            phones.update(re.findall(pat, text))
        
        # Email
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        emails.update(re.findall(email_pattern, text))
        
        # Alamat
        address_patterns = [
            r'\b\d{1,5}\s+[A-Za-z]+\s+(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|way|place|pl|court|ct)\b.*?(?:[A-Z]{2}\s?\d{5}(?:-\d{4})?)?',
            r'\b\d{5}(?:-\d{4})?\b',
            r'\b[A-Z][a-z]+,\s*[A-Z]{2}\s*\d{5}\b',
        ]
        for pat in address_patterns:
            addresses.update(re.findall(pat, text, re.IGNORECASE))
        
        return phones, emails, addresses

    # ---------- MODUL DORKING UTAMA ----------
    def google_dork_phone_email_address(self, query: str) -> List[Dict]:
        """
        Dorking Google untuk mencari kontak. Kali ini kita ekstrak langsung dari snippet.
        """
        dorks = [
            f'"{query}" "phone" -site:gov -site:mil',
            f'"{query}" "email" -site:gov -site:mil',
            f'"{query}" "address" -site:gov -site:mil',
            f'"{query}" "tel:" -site:gov -site:mil',
            f'"{query}" "mobile" -site:gov -site:mil',
            f'"{query}" "contact" -site:gov -site:mil',
            f'"{query}" "whatsapp" -site:gov -site:mil',
            f'intitle:"{query}" "contact"',
            f'inurl:"{query}" "phone" filetype:pdf',
            f'inurl:"{query}" "email" filetype:pdf',
        ]
        all_mentions = []
        for dork in dorks:
            encoded = urllib.parse.quote(dork)
            url = f"https://www.google.com/search?q={encoded}&num=30"
            html = self._fetch(url)
            if not html:
                continue
            
            # Ekstrak hasil dengan BeautifulSoup jika tersedia
            if HAS_SOUP:
                soup = BeautifulSoup(html, 'html.parser')
                # Cari elemen hasil (biasanya div dengan class tertentu)
                result_blocks = soup.find_all('div', class_='g')
                for block in result_blocks[:20]:
                    title_tag = block.find('h3')
                    title = title_tag.get_text(strip=True) if title_tag else ''
                    link_tag = block.find('a')
                    url_clean = link_tag.get('href') if link_tag else ''
                    snippet_tag = block.find('div', class_='VwiC3b')
                    snippet = snippet_tag.get_text(strip=True) if snippet_tag else ''
                    
                    # Gabungkan semua teks untuk ekstraksi
                    full_text = f"{title} {snippet} {url_clean}"
                    phones, emails, addresses = self._extract_contacts_from_text(full_text)
                    
                    # Simpan jika ada kontak
                    if phones or emails or addresses:
                        all_mentions.append({
                            'source': 'Google Dork',
                            'title': title[:200],
                            'url': url_clean,
                            'snippet': snippet[:500],
                            'phones': list(phones),
                            'emails': list(emails),
                            'addresses': list(addresses),
                            'dork': dork
                        })
            else:
                # Fallback regex sederhana
                pattern = r'<a href="\/url\?q=(https?://[^&"]+)&[^"]*"[^>]*>(.*?)</a>'
                matches = re.findall(pattern, html, re.IGNORECASE)
                for url_clean, title in matches[:20]:
                    title_clean = re.sub(r'<[^>]+>', '', title).strip()
                    # Ekstrak kontak dari judul dan URL
                    full_text = f"{title_clean} {url_clean}"
                    phones, emails, addresses = self._extract_contacts_from_text(full_text)
                    if phones or emails or addresses:
                        all_mentions.append({
                            'source': 'Google Dork (regex)',
                            'title': title_clean[:200],
                            'url': url_clean,
                            'snippet': '',
                            'phones': list(phones),
                            'emails': list(emails),
                            'addresses': list(addresses),
                            'dork': dork
                        })
            time.sleep(1)
            if len(all_mentions) > 100:
                break
        return all_mentions

    # ---------- WHITE PAGES ----------
    def search_white_pages(self, name: str) -> Dict[str, Set[str]]:
        sources = {
            'whitepages': f'https://www.whitepages.com/name/{urllib.parse.quote(name)}',
            'pipl': f'https://pipl.com/search/?q={urllib.parse.quote(name)}',
            'thatsthem': f'https://thatsthem.com/name/{urllib.parse.quote(name)}',
            'spokeo': f'https://www.spokeo.com/{urllib.parse.quote(name)}',
            'zabasearch': f'https://www.zabasearch.com/people/{urllib.parse.quote(name)}/'
        }
        results = {'phones': set(), 'emails': set(), 'addresses': set()}
        
        for site, url in sources.items():
            html = self._fetch(url)
            if not html:
                continue
            # Ekstrak dengan BeautifulSoup jika tersedia
            if HAS_SOUP:
                soup = BeautifulSoup(html, 'html.parser')
                text = soup.get_text()
                phones, emails, addresses = self._extract_contacts_from_text(text)
            else:
                phones, emails, addresses = self._extract_contacts_from_text(html)
            
            results['phones'].update(phones)
            results['emails'].update(emails)
            results['addresses'].update(addresses)
            
            time.sleep(1.5)
            logger.info(f"Hasil dari {site}: {len(phones)} telepon, {len(emails)} email, {len(addresses)} alamat")
        
        return results

    # ---------- HAVE I BEEN PWNED ----------
    def search_haveibeenpwned(self, email: str) -> List[str]:
        try:
            url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{urllib.parse.quote(email)}"
            resp = self.session.get(url, headers={'hibp-api-key': ''}, timeout=TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return [b['Name'] for b in data]
            elif resp.status_code == 404:
                return []
            else:
                return []
        except:
            return []

    # ---------- ORKESTRASI ----------
    def run(self) -> ContactInfo:
        logger.info(f"Memulai deep OSINT untuk: {self.full_name}")
        
        # 1. Google Dorking dengan variasi nama
        all_mentions = []
        for variant in self._variants[:4]:
            mentions = self.google_dork_phone_email_address(variant)
            all_mentions.extend(mentions)
            logger.info(f"Dork '{variant}' menghasilkan {len(mentions)} hasil dengan kontak")
        
        # 2. White Pages & direktori publik
        wp_results = self.search_white_pages(self.full_name)
        self.info.phones.update(wp_results['phones'])
        self.info.emails.update(wp_results['emails'])
        self.info.addresses.update(wp_results['addresses'])
        
        # 3. Ekstrak dari semua hasil Google (menggunakan hasil yang sudah diekstrak)
        for mention in all_mentions:
            self.info.phones.update(mention.get('phones', []))
            self.info.emails.update(mention.get('emails', []))
            self.info.addresses.update(mention.get('addresses', []))
        
        # 4. Periksa breach untuk setiap email
        for email in list(self.info.emails)[:5]:
            breaches = self.search_haveibeenpwned(email)
            if breaches:
                self.info.raw_mentions.append({
                    'type': 'breach',
                    'email': email,
                    'breaches': breaches
                })
                logger.info(f"Email {email} muncul di breach: {', '.join(breaches)}")
        
        # 5. Simpan mentah
        self.info.raw_mentions.extend(all_mentions[:100])
        logger.info(f"Deep OSINT selesai. Ditemukan: {len(self.info.phones)} telepon, {len(self.info.emails)} email, {len(self.info.addresses)} alamat")
        return self.info

    def save_txt(self, info: ContactInfo) -> Path:
        filename = OUTPUT_DIR / f"{info.name.replace(' ', '_')}_osint_report.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("="*60 + "\n")
            f.write(f"LAPORAN OSINT DEEP DORKING\n")
            f.write(f"Nama: {info.name}\n")
            f.write(f"Tanggal: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*60 + "\n\n")
            
            f.write("NOMOR TELEPON:\n")
            if info.phones:
                for phone in sorted(info.phones):
                    f.write(f"  - {phone}\n")
            else:
                f.write("  (Tidak ditemukan)\n")
            f.write("\n")
            
            f.write("EMAIL:\n")
            if info.emails:
                for email in sorted(info.emails):
                    f.write(f"  - {email}\n")
            else:
                f.write("  (Tidak ditemukan)\n")
            f.write("\n")
            
            f.write("ALAMAT:\n")
            if info.addresses:
                for addr in sorted(info.addresses):
                    f.write(f"  - {addr}\n")
            else:
                f.write("  (Tidak ditemukan)\n")
            f.write("\n")
            
            f.write("USERNAME / MEDIA SOSIAL:\n")
            if info.usernames:
                for uname in sorted(info.usernames):
                    f.write(f"  - {uname}\n")
            else:
                f.write("  (Tidak ditemukan)\n")
            f.write("\n")
            
            f.write("LINK SOSIAL (diekstrak):\n")
            if info.social_links:
                for platform, url in info.social_links.items():
                    f.write(f"  - {platform}: {url}\n")
            else:
                f.write("  (Tidak ditemukan)\n")
            f.write("\n")
            
            f.write("CATATAN BREACH:\n")
            for item in info.raw_mentions:
                if isinstance(item, dict) and item.get('type') == 'breach':
                    f.write(f"  - Email {item['email']} terpapar di: {', '.join(item['breaches'])}\n")
            f.write("\n")
            
            f.write("MENTIONS DARI GOOGLE (sampel):\n")
            count = 0
            for item in info.raw_mentions:
                if isinstance(item, dict) and item.get('source', '').startswith('Google'):
                    f.write(f"  - {item.get('title', '')[:80]}... -> {item.get('url', '')}\n")
                    count += 1
                    if count >= 20:
                        break
            f.write("\n" + "="*60 + "\n")
            f.write("Akhir laporan. Data diambil dari sumber publik.\n")
        
        logger.info(f"Laporan teks disimpan di {filename}")
        return filename

# ---------- ENTRY POINT ----------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deep OSINT Dorking — Cari telepon, email, alamat dari nama")
    parser.add_argument("name", type=str, help="Nama lengkap, misal 'John Doe'")
    parser.add_argument("--verbose", "-v", action="store_true", help="Tampilkan log detail")
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    engine = DeepDorkEngine(args.name)
    info = engine.run()
    txt_path = engine.save_txt(info)
    
    print("\n" + "="*60)
    print(f"OSINT DEEP DORKING SELESAI UNTUK: {info.name}")
    print("="*60)
    print(f"Nomor telepon ditemukan: {len(info.phones)}")
    print(f"Email ditemukan: {len(info.emails)}")
    print(f"Alamat ditemukan: {len(info.addresses)}")
    print(f"Username ditemukan: {len(info.usernames)}")
    print(f"\nLaporan lengkap: {txt_path}")
    print("="*60)

if __name__ == "__main__":
    main()
