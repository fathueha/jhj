#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Deep Dork Advanced v3.1 (Tanpa pandas)
Penulis: Lyra untuk Kael
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
import csv
import concurrent.futures
from urllib.parse import quote_plus
from typing import List, Dict, Set, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import yagooglesearch
except ImportError:
    yagooglesearch = None

# ---------- Konfigurasi ----------
VERSION = "3.1.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DEFAULT_DELAY_MIN = 37
DEFAULT_DELAY_MAX = 60
DEFAULT_MAX_RESULTS = 100
USER_AGENT = UserAgent()

# Daftar dork bawaan untuk kontak
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
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text(separator=' ', strip=True)
    
    phones = set()
    emails = set()
    addresses = set()
    
    phone_patterns = [
        r'\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{1,4}',
        r'\b0\d{2,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b',
        r'\b\d{3}[\s\-]?\d{3}[\s\-]?\d{4}\b',
        r'\(\d{3}\)\s?\d{3}[\s\-]?\d{4}',
        r'\b\d{3}\.\d{3}\.\d{4}\b',
    ]
    for pat in phone_patterns:
        for m in re.findall(pat, text):
            if len(m) >= 7:
                phones.add(m.strip())
    
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails.update(re.findall(email_pattern, text))
    
    address_patterns = [
        r'\b\d{1,5}\s+[A-Za-z]+\s+(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|way|place|pl|court|ct)\b.*?(?:[A-Z]{2}\s?\d{5}(?:-\d{4})?)?',
        r'\b\d{5}(?:-\d{4})?\b',
        r'\b[A-Z][a-z]+,\s*[A-Z]{2}\s*\d{5}\b',
    ]
    for pat in address_patterns:
        for m in re.findall(pat, text, re.IGNORECASE):
            if len(m) > 10:
                addresses.add(m.strip())
    
    return phones, emails, addresses

def search_google(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
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
            logging.warning(f"yagooglesearch gagal: {e}, fallback ke kosong")
    return []

def search_bing(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
    urls = []
    session = setup_session(proxy, verify_ssl)
    headers = {'User-Agent': USER_AGENT.random}
    for page in range(0, min(max_results, 1000), 10):
        params = {'q': query, 'count': 10, 'first': page + 1}
        try:
            resp = session.get('https://www.bing.com/search', params=params, headers=headers, timeout=20)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.select('li.b_algo h2 a'):
                href = a.get('href')
                if href and href.startswith('http'):
                    urls.append(href)
            if 'captcha' in resp.text.lower():
                logging.warning("Bing CAPTCHA detected")
                break
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            logging.error(f"Bing error: {e}")
            break
        if len(urls) >= max_results:
            break
    return urls[:max_results]

def search_duckduckgo(query: str, max_results: int = 100, proxy: str = None, verify_ssl: bool = True) -> List[str]:
    urls = []
    session = setup_session(proxy, verify_ssl)
    params = {'q': query, 's': 0}
    try:
        resp = session.get('https://lite.duckduckgo.com/lite/', params=params, timeout=20)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tr in soup.find_all('tr', class_='result'):
            td = tr.find('td')
            if td:
                a = td.find('a')
                if a and a.get('href') and a['href'].startswith('http'):
                    urls.append(a['href'])
        if 'captcha' in resp.text.lower():
            logging.warning("DuckDuckGo CAPTCHA detected")
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
        self.all_contacts = {'phones': set(), 'emails': set(), 'addresses': set()}
        self.results = []
        self.session = setup_session(verify_ssl=verify_ssl)
    
    def _get_proxy(self):
        return random.choice(self.proxies) if self.proxies else None
    
    def _search_engine(self, engine: str, query: str) -> List[str]:
        engine_map = {
            'google': search_google,
            'bing': search_bing,
            'duckduckgo': search_duckduckgo,
        }
        func = engine_map.get(engine.lower())
        if func:
            return func(query, self.max_results, self._get_proxy(), self.verify_ssl)
        self.log.warning(f"Engine {engine} tidak dikenal")
        return []
    
    def _visit_url_and_extract(self, url: str) -> Dict:
        if not self.extract_contacts:
            return {}
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                phones, emails, addresses = extract_contacts(resp.text)
                return {'url': url, 'phones': list(phones), 'emails': list(emails), 'addresses': list(addresses)}
        except Exception:
            pass
        return {}
    
    def _check_breach(self, emails: Set[str]) -> Dict:
        if not self.check_breach or not emails:
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
        self.log.info(f"Deep Dork Advanced v{VERSION} dimulai")
        self.log.info(f"Dorks: {len(self.dorks)}, Domain: {self.domain or '(none)'}")
        self.log.info(f"Mesin: {', '.join(self.engines)}")
        
        for idx, dork in enumerate(self.dorks, 1):
            query = f"site:{self.domain} {dork}" if self.domain else dork
            self.log.info(f"({idx}/{len(self.dorks)}) Eksekusi: {query}")
            
            dork_result = {
                'dork': dork,
                'query': query,
                'engine_results': {},
                'total_urls': 0,
                'urls': [],
                'contacts': {'phones': [], 'emails': [], 'addresses': []},
            }
            
            for engine in self.engines:
                try:
                    urls = self._search_engine(engine, query)
                    self.log.info(f"  {engine}: {len(urls)} URL")
                    dork_result['engine_results'][engine] = urls
                    dork_result['urls'].extend(urls)
                except Exception as e:
                    self.log.error(f"  {engine} error: {e}")
                    dork_result['engine_results'][engine] = []
            
            unique_urls = list(set(dork_result['urls']))
            dork_result['urls'] = unique_urls
            dork_result['total_urls'] = len(unique_urls)
            self.total_urls += len(unique_urls)
            
            if self.extract_contacts and unique_urls:
                self.log.info(f"  Mengunjungi {len(unique_urls)} URL untuk ekstraksi kontak...")
                all_phones, all_emails, all_addresses = set(), set(), set()
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(self._visit_url_and_extract, url) for url in unique_urls]
                    for future in concurrent.futures.as_completed(futures):
                        data = future.result()
                        if data:
                            all_phones.update(data.get('phones', []))
                            all_emails.update(data.get('emails', []))
                            all_addresses.update(data.get('addresses', []))
                
                dork_result['contacts']['phones'] = list(all_phones)
                dork_result['contacts']['emails'] = list(all_emails)
                dork_result['contacts']['addresses'] = list(all_addresses)
                
                self.all_contacts['phones'].update(all_phones)
                self.all_contacts['emails'].update(all_emails)
                self.all_contacts['addresses'].update(all_addresses)
                
                self.log.info(f"  Ditemukan: {len(all_phones)} telepon, {len(all_emails)} email, {len(all_addresses)} alamat")
            
            self.results.append(dork_result)
            
            if idx < len(self.dorks):
                delay = random.uniform(self.min_delay, self.max_delay)
                self.log.info(f"  Jeda {delay:.1f} detik...")
                time.sleep(delay)
        
        # Cek breach
        breached = {}
        if self.check_breach and self.all_contacts['emails']:
            self.log.info(f"Memeriksa {len(self.all_contacts['emails'])} email di HIBP...")
            breached = self._check_breach(self.all_contacts['emails'])
        
        self._save_results(breached)
        self.log.info(f"Selesai. Total URL unik: {self.total_urls}")
        self.log.info(f"Kontak: {len(self.all_contacts['phones'])} telepon, {len(self.all_contacts['emails'])} email, {len(self.all_contacts['addresses'])} alamat")
    
    def _save_results(self, breached: Dict):
        # JSON
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
        self.log.info(f"JSON: {json_file}")
        
        # CSV (tanpa pandas)
        csv_file = f"{self.output_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['dork', 'url', 'phones', 'emails', 'addresses'])
            for res in self.results:
                for url in res['urls']:
                    writer.writerow([
                        res['dork'],
                        url,
                        '; '.join(res['contacts']['phones']),
                        '; '.join(res['contacts']['emails']),
                        '; '.join(res['contacts']['addresses']),
                    ])
        self.log.info(f"CSV: {csv_file}")
        
        # TXT
        txt_file = f"{self.output_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(f"DEEP DORK ADVANCED v{VERSION}\n")
            f.write(f"Tanggal: {datetime.datetime.now().isoformat()}\n")
            f.write(f"Total URL unik: {self.total_urls}\n")
            f.write(f"Telepon: {len(self.all_contacts['phones'])}\n")
            f.write(f"Email: {len(self.all_contacts['emails'])}\n")
            f.write(f"Alamat: {len(self.all_contacts['addresses'])}\n\n")
            f.write("--- KONTAK ---\n")
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
        self.log.info(f"TXT: {txt_file}")

# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description=f"Deep Dork Advanced v{VERSION}")
    parser.add_argument('-g', '--google-dorks', type=str, help='File dork (satu per baris)')
    parser.add_argument('-d', '--domain', type=str, help='Domain batasan')
    parser.add_argument('-m', '--max-results', type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument('-i', '--min-delay', type=int, default=DEFAULT_DELAY_MIN)
    parser.add_argument('-x', '--max-delay', type=int, default=DEFAULT_DELAY_MAX)
    parser.add_argument('-p', '--proxies', type=str, help='Proxy comma separated')
    parser.add_argument('--no-verify-ssl', action='store_true')
    parser.add_argument('--engines', type=str, default='google,bing,duckduckgo')
    parser.add_argument('--no-extract', action='store_true')
    parser.add_argument('--no-breach', action='store_true')
    parser.add_argument('-o', '--output-prefix', type=str, default='deep_dork')
    parser.add_argument('-v', '--verbose', action='store_true')
    
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO if not args.verbose else logging.DEBUG, format=LOG_FORMAT)
    
    if args.google_dorks and os.path.exists(args.google_dorks):
        with open(args.google_dorks, 'r', encoding='utf-8') as f:
            dorks = [line.strip() for line in f if line.strip()]
    else:
        dorks = DEFAULT_DORKS
        logging.warning("Tidak ada file dork, menggunakan bawaan.")
    
    proxies = args.proxies.split(',') if args.proxies else []
    engines = [e.strip() for e in args.engines.split(',') if e.strip()]
    
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
