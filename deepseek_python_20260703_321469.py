#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Deep Dork Advanced v3.0
Penulis: Lyra untuk Kael
Deskripsi: OSINT multi-mesin pencari + ekstraktor kontak (telepon, email, alamat)
Fitur:
- Google, Bing, DuckDuckGo
- Proxy & User-Agent rotasi
- Ekstraksi otomatis dari halaman hasil
- Validasi data kontak
- Ekspor JSON, CSV, TXT
- Deteksi CAPTCHA
"""

import argparse
import datetime
import json
import logging
import os
import random
import re
import sys
import time
import concurrent.futures
from urllib.parse import urlparse, quote_plus
from typing import List, Dict, Set, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional: jika tidak tersedia, kita fallback ke requests saja
try:
    import yagooglesearch
except ImportError:
    yagooglesearch = None

# ---------- Konfigurasi ----------
VERSION = "3.0.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DEFAULT_DELAY_MIN = 37
DEFAULT_DELAY_MAX = 60
DEFAULT_MAX_RESULTS = 100
USER_AGENT = UserAgent()
SESSION = None  # akan diinisialisasi nanti

# Daftar dork bawaan untuk kontak (bisa ditambah)
DEFAULT_DORKS = [
    '"phone" "email"',
    '"contact" "phone"',
    '"mobile" "address"',
    '"tel:" "email"',
    'intitle:"contact" "phone"',
    'inurl:"contact" "email"',
    'filetype:pdf "phone" "email"',
]

# ---------- Fungsi Bantu ----------
def setup_session(proxy: Optional[str] = None, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retries))
    session.mount('https://', HTTPAdapter(max_retries=retries))
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    session.verify = verify_ssl
    session.headers.update({
        'User-Agent': USER_AGENT.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'DNT': '1'
    })
    return session

def random_delay(min_sec: int, max_sec: int):
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)
    return delay

def extract_contacts(html: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Ekstrak nomor telepon, email, dan alamat dari teks HTML.
    """
    # Hapus tag untuk teks bersih
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text(separator=' ', strip=True)
    
    phones = set()
    emails = set()
    addresses = set()
    
    # Pola telepon (internasional, lokal, dengan pemisah)
    phone_patterns = [
        r'\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{1,4}',
        r'\b0\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b',
        r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b',
        r'\(\d{3}\)\s?\d{3}[\s\-]?\d{4}',
        r'\b\d{3}\.\d{3}\.\d{4}\b',
    ]
    for pat in phone_patterns:
        matches = re.findall(pat, text)
        for m in matches:
            # Filter panjang minimal
            if len(m) >= 7:
                phones.add(m.strip())
    
    # Email
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails.update(re.findall(email_pattern, text))
    
    # Alamat (heuristic)
    address_patterns = [
        r'\b\d{1,5}\s+[A-Za-z]+\s+(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|way|place|pl|court|ct)\b.*?(?:[A-Z]{2}\s?\d{5}(?:-\d{4})?)?',
        r'\b\d{5}(?:-\d{4})?\b',
        r'\b[A-Z][a-z]+,\s*[A-Z]{2}\s*\d{5}\b',
    ]
    for pat in address_patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        for m in matches:
            if len(m) > 10:
                addresses.add(m.strip())
    
    return phones, emails, addresses

def search_google(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
    """Menggunakan yagooglesearch jika tersedia, fallback ke requests."""
    if yagooglesearch:
        try:
            client = yagooglesearch.SearchClient(
                query,
                tbs="li:1",
                num=100,
                max_search_result_urls_to_return=max_results,
                proxy=proxy,
                verify_ssl=verify_ssl,
                verbosity=0,
            )
            client.assign_random_user_agent()
            return client.search()
        except Exception as e:
            logging.warning(f"yagooglesearch gagal: {e}, fallback ke requests")
    
    # Fallback: requests + BeautifulSoup (simplifikasi)
    # Sebenarnya kita bisa scraping hasil Google, tapi ini rentan blokir.
    # Kita akan gunakan pendekatan sederhana: tidak diimplementasikan di sini.
    # Untuk fallback, kita kembalikan list kosong.
    logging.warning("Google search fallback tidak diimplementasikan; menggunakan hasil kosong.")
    return []

def search_bing(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
    """Scrape hasil pencarian Bing."""
    urls = []
    session = setup_session(proxy, verify_ssl)
    headers = {
        'User-Agent': USER_AGENT.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    # Bing menggunakan parameter count untuk jumlah hasil per halaman
    # Kita akan loop beberapa halaman
    for page in range(0, min(max_results, 1000), 10):
        params = {
            'q': query,
            'count': 10,
            'first': page + 1,
        }
        url = 'https://www.bing.com/search'
        try:
            resp = session.get(url, params=params, headers=headers, timeout=20)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Cari semua link hasil
            for a in soup.select('li.b_algo h2 a'):
                href = a.get('href')
                if href and href.startswith('http'):
                    urls.append(href)
            # Deteksi CAPTCHA
            if 'captcha' in resp.text.lower():
                logging.warning("Bing meminta CAPTCHA, hentikan.")
                break
            # Jeda antar halaman
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logging.error(f"Bing error: {e}")
            break
        if len(urls) >= max_results:
            break
    return urls[:max_results]

def search_duckduckgo(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
    """Scrape hasil pencarian DuckDuckGo (menggunakan lite version)."""
    urls = []
    session = setup_session(proxy, verify_ssl)
    # Gunakan versi lite untuk parsing mudah
    url = 'https://lite.duckduckgo.com/lite/'
    params = {
        'q': query,
        's': 0,
    }
    headers = {'User-Agent': USER_AGENT.random}
    try:
        resp = session.get(url, params=params, headers=headers, timeout=20)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Di lite, hasil ada di tabel dengan class 'result'
        for tr in soup.find_all('tr', class_='result'):
            td = tr.find('td')
            if td:
                a = td.find('a')
                if a and a.get('href'):
                    href = a['href']
                    if href.startswith('http'):
                        urls.append(href)
        # Deteksi CAPTCHA
        if 'captcha' in resp.text.lower():
            logging.warning("DuckDuckGo meminta CAPTCHA.")
    except Exception as e:
        logging.error(f"DuckDuckGo error: {e}")
    return urls[:max_results]

# ---------- Mesin Utama ----------
class DeepDorkEngine:
    def __init__(
        self,
        dorks: List[str],
        domain: str = "",
        max_results: int = DEFAULT_MAX_RESULTS,
        min_delay: int = DEFAULT_DELAY_MIN,
        max_delay: int = DEFAULT_DELAY_MAX,
        proxies: List[str] = None,
        verify_ssl: bool = True,
        output_prefix: str = "deep_dork",
        engines: List[str] = ['google', 'bing', 'duckduckgo'],
        extract_contacts: bool = True,
        check_breach: bool = True,
    ):
        self.dorks = dorks
        self.domain = domain
        self.max_results = max_results
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.proxies = proxies or []
        self.verify_ssl = verify_ssl
        self.output_prefix = output_prefix
        self.engines = engines
        self.extract_contacts = extract_contacts
        self.check_breach = check_breach
        
        self.log = logging.getLogger('DeepDork')
        self.total_urls = 0
        self.all_contacts = {
            'phones': set(),
            'emails': set(),
            'addresses': set(),
        }
        self.results = []  # List of dict per dork
        
        # Siapkan sesi untuk scraping halaman
        self.session = setup_session(verify_ssl=verify_ssl)
    
    def _get_proxy(self):
        if self.proxies:
            return random.choice(self.proxies)
        return None
    
    def _search_engine(self, engine: str, query: str) -> List[str]:
        if engine.lower() == 'google':
            return search_google(query, self.max_results, self._get_proxy(), self.verify_ssl)
        elif engine.lower() == 'bing':
            return search_bing(query, self.max_results, self._get_proxy(), self.verify_ssl)
        elif engine.lower() == 'duckduckgo':
            return search_duckduckgo(query, self.max_results, self._get_proxy(), self.verify_ssl)
        else:
            self.log.warning(f"Engine {engine} tidak dikenal.")
            return []
    
    def _visit_url_and_extract(self, url: str) -> Dict:
        """Kunjungi URL, ambil konten, ekstrak kontak."""
        if not self.extract_contacts:
            return {}
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                phones, emails, addresses = extract_contacts(resp.text)
                return {
                    'url': url,
                    'phones': list(phones),
                    'emails': list(emails),
                    'addresses': list(addresses),
                }
        except Exception as e:
            self.log.debug(f"Gagal mengunjungi {url}: {e}")
        return {}
    
    def _check_breach(self, emails: Set[str]) -> Dict:
        """Periksa email di Have I Been Pwned."""
        if not self.check_breach:
            return {}
        breached = {}
        for email in emails:
            try:
                url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote_plus(email)}"
                resp = requests.get(url, headers={'hibp-api-key': ''}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    breached[email] = [b['Name'] for b in data]
                elif resp.status_code == 404:
                    breached[email] = []
                else:
                    breached[email] = ['Error']
                time.sleep(0.5)
            except:
                breached[email] = ['Error']
        return breached
    
    def run(self):
        self.log.info(f"Memulai Deep Dork Advanced v{VERSION}")
        self.log.info(f"Dorks: {len(self.dorks)}, Domain: {self.domain or '(none)'}")
        self.log.info(f"Mesin: {', '.join(self.engines)}")
        
        timestamp_start = datetime.datetime.now().isoformat()
        
        dork_count = 0
        for dork in self.dorks:
            dork_count += 1
            query = f"site:{self.domain} {dork}" if self.domain else dork
            self.log.info(f"({dork_count}/{len(self.dorks)}) Mengeksekusi: {query}")
            
            dork_result = {
                'dork': dork,
                'query': query,
                'engine_results': {},
                'total_urls': 0,
                'urls': [],
                'contacts': {'phones': [], 'emails': [], 'addresses': []},
            }
            
            # Cari di setiap mesin
            for engine in self.engines:
                try:
                    urls = self._search_engine(engine, query)
                    self.log.info(f"  {engine}: {len(urls)} URL ditemukan")
                    dork_result['engine_results'][engine] = urls
                    dork_result['urls'].extend(urls)
                except Exception as e:
                    self.log.error(f"  {engine} error: {e}")
                    dork_result['engine_results'][engine] = []
            
            # Unique URLs
            unique_urls = list(set(dork_result['urls']))
            dork_result['urls'] = unique_urls
            dork_result['total_urls'] = len(unique_urls)
            self.total_urls += len(unique_urls)
            
            # Ekstrak kontak dari setiap URL (parallel)
            if self.extract_contacts and unique_urls:
                self.log.info(f"  Mengunjungi {len(unique_urls)} URL untuk ekstraksi kontak...")
                all_phones = set()
                all_emails = set()
                all_addresses = set()
                # Gunakan thread pool untuk kecepatan
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_url = {executor.submit(self._visit_url_and_extract, url): url for url in unique_urls}
                    for future in concurrent.futures.as_completed(future_to_url):
                        url = future_to_url[future]
                        try:
                            data = future.result()
                            if data:
                                all_phones.update(data.get('phones', []))
                                all_emails.update(data.get('emails', []))
                                all_addresses.update(data.get('addresses', []))
                        except Exception as e:
                            self.log.debug(f"Thread error untuk {url}: {e}")
                
                dork_result['contacts']['phones'] = list(all_phones)
                dork_result['contacts']['emails'] = list(all_emails)
                dork_result['contacts']['addresses'] = list(all_addresses)
                
                # Update master contacts
                self.all_contacts['phones'].update(all_phones)
                self.all_contacts['emails'].update(all_emails)
                self.all_contacts['addresses'].update(all_addresses)
                
                self.log.info(f"  Ditemukan: {len(all_phones)} telepon, {len(all_emails)} email, {len(all_addresses)} alamat")
            
            self.results.append(dork_result)
            
            # Jeda antar dork
            if dork_count < len(self.dorks):
                delay = random.uniform(self.min_delay, self.max_delay)
                self.log.info(f"  Jeda {delay:.1f} detik...")
                time.sleep(delay)
        
        # Periksa breach untuk email yang ditemukan
        breached = {}
        if self.check_breach and self.all_contacts['emails']:
            self.log.info(f"Memeriksa {len(self.all_contacts['emails'])} email di Have I Been Pwned...")
            breached = self._check_breach(self.all_contacts['emails'])
        
        # Simpan hasil
        self._save_results(breached)
        
        timestamp_end = datetime.datetime.now().isoformat()
        self.log.info(f"Selesai. Total URL unik: {self.total_urls}")
        self.log.info(f"Kontak: {len(self.all_contacts['phones'])} telepon, {len(self.all_contacts['emails'])} email, {len(self.all_contacts['addresses'])} alamat")
        self.log.info(f"Hasil disimpan dengan prefix: {self.output_prefix}")
    
    def _save_results(self, breached: Dict):
        # Simpan JSON
        json_file = f"{self.output_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump({
                'version': VERSION,
                'timestamp': datetime.datetime.now().isoformat(),
                'total_urls': self.total_urls,
                'contacts': {k: list(v) for k, v in self.all_contacts.items()},
                'breached_emails': breached,
                'results': self.results,
            }, f, indent=2)
        self.log.info(f"JSON saved: {json_file}")
        
        # Simpan CSV
        csv_file = f"{self.output_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        rows = []
        for res in self.results:
            for url in res['urls']:
                rows.append({
                    'dork': res['dork'],
                    'url': url,
                    'phones': ', '.join(res['contacts']['phones']),
                    'emails': ', '.join(res['contacts']['emails']),
                    'addresses': ', '.join(res['contacts']['addresses']),
                })
        if rows:
            df = pd.DataFrame(rows)
            df.to_csv(csv_file, index=False, encoding='utf-8')
            self.log.info(f"CSV saved: {csv_file}")
        
        # Simpan TXT (format sederhana)
        txt_file = f"{self.output_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(f"DEEP DORK ADVANCED v{VERSION}\n")
            f.write(f"Tanggal: {datetime.datetime.now().isoformat()}\n")
            f.write(f"Total URL unik: {self.total_urls}\n")
            f.write(f"Telepon: {len(self.all_contacts['phones'])}\n")
            f.write(f"Email: {len(self.all_contacts['emails'])}\n")
            f.write(f"Alamat: {len(self.all_contacts['addresses'])}\n")
            f.write("\n--- KONTAK ---\n")
            f.write("Telepon:\n")
            for p in sorted(self.all_contacts['phones']):
                f.write(f"  {p}\n")
            f.write("Email:\n")
            for e in sorted(self.all_contacts['emails']):
                breach_info = breached.get(e, [])
                if breach_info:
                    f.write(f"  {e} (TERPAPAR: {', '.join(breach_info)})\n")
                else:
                    f.write(f"  {e}\n")
            f.write("Alamat:\n")
            for a in sorted(self.all_contacts['addresses']):
                f.write(f"  {a}\n")
            f.write("\n--- DETAIL HASIL PER DORK ---\n")
            for res in self.results:
                f.write(f"\nDork: {res['dork']}\n")
                f.write(f"Query: {res['query']}\n")
                f.write(f"Total URL: {res['total_urls']}\n")
                f.write("URL:\n")
                for url in res['urls']:
                    f.write(f"  {url}\n")
        self.log.info(f"TXT saved: {txt_file}")

# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description=f"Deep Dork Advanced v{VERSION} - OSINT multi-mesin + ekstraktor kontak")
    parser.add_argument('-g', '--google-dorks', type=str, help='File berisi daftar dork (satu per baris)')
    parser.add_argument('-d', '--domain', type=str, help='Domain untuk dibatasi (opsional)')
    parser.add_argument('-m', '--max-results', type=int, default=DEFAULT_MAX_RESULTS, help=f'Maks URL per dork per mesin (default {DEFAULT_MAX_RESULTS})')
    parser.add_argument('-i', '--min-delay', type=int, default=DEFAULT_DELAY_MIN, help=f'Jeda minimum antar dork (detik, default {DEFAULT_DELAY_MIN})')
    parser.add_argument('-x', '--max-delay', type=int, default=DEFAULT_DELAY_MAX, help=f'Jeda maksimum (detik, default {DEFAULT_DELAY_MAX})')
    parser.add_argument('-p', '--proxies', type=str, help='Proxy terpisah koma (contoh: socks5://127.0.0.1:9050,http://proxy:8080)')
    parser.add_argument('--no-verify-ssl', action='store_true', help='Nonaktifkan verifikasi SSL')
    parser.add_argument('--engines', type=str, default='google,bing,duckduckgo', help='Mesin pencari, pisah koma (default: google,bing,duckduckgo)')
    parser.add_argument('--no-extract', action='store_true', help='Nonaktifkan ekstraksi kontak (hanya kumpulkan URL)')
    parser.add_argument('--no-breach', action='store_true', help='Nonaktifkan pengecekan breach email')
    parser.add_argument('-o', '--output-prefix', type=str, default='deep_dork', help='Prefiks file output (default: deep_dork)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Mode verbose')
    
    args = parser.parse_args()
    
    # Logging
    logging.basicConfig(level=logging.INFO if not args.verbose else logging.DEBUG, format=LOG_FORMAT)
    
    # Dorks
    if args.google_dorks and os.path.exists(args.google_dorks):
        with open(args.google_dorks, 'r', encoding='utf-8') as f:
            dorks = [line.strip() for line in f if line.strip()]
    else:
        # Gunakan dork bawaan
        dorks = DEFAULT_DORKS
        logging.warning("Tidak ada file dork, menggunakan dork bawaan.")
    
    # Proxies
    proxies = args.proxies.split(',') if args.proxies else []
    
    # Engines
    engines = [e.strip() for e in args.engines.split(',') if e.strip()]
    
    # Jalankan
    engine = DeepDorkEngine(
        dorks=dorks,
        domain=args.domain,
        max_results=args.max_results,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        proxies=proxies,
        verify_ssl=not args.no_verify_ssl,
        output_prefix=args.output_prefix,
        engines=engines,
        extract_contacts=not args.no_extract,
        check_breach=not args.no_breach,
    )
    engine.run()

if __name__ == "__main__":
    main()
