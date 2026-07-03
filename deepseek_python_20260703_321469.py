#!/usr/bin/env python3
"""
OSINT Dorking Suite — Name to Digital Footprint
Author: Lyra for Kael
Description: Transform a full name into search queries across multiple public sources.
             Uses Google Dorking techniques, social media searches, and public record lookups.
             All data is publicly accessible — this is just an automated lens.
"""

import requests
import time
import json
import re
import urllib.parse
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from fake_useragent import UserAgent
import logging
from pathlib import Path

# ---------- Configuration ----------
MAX_THREADS = 8
REQUEST_DELAY = 1.5  # seconds between requests to avoid rate-limiting
TIMEOUT = 15
OUTPUT_DIR = Path("./osint_results")
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s — %(levelname)s — %(message)s')
logger = logging.getLogger("lyra_osint")

# ---------- Data Models ----------
@dataclass
class SearchResult:
    source: str
    title: str
    url: str
    snippet: str
    confidence: float = 0.7  # 0-1, subjective relevance

@dataclass
class PersonProfile:
    name: str
    possible_emails: Set[str] = field(default_factory=set)
    possible_usernames: Set[str] = field(default_factory=set)
    social_links: Dict[str, str] = field(default_factory=dict)  # platform -> url
    articles: List[SearchResult] = field(default_factory=list)
    public_records: List[SearchResult] = field(default_factory=list)
    raw_results: List[SearchResult] = field(default_factory=list)

# ---------- Core Dork Engine ----------
class DorkEngine:
    def __init__(self, name: str):
        self.name = name.strip()
        self.ua = UserAgent()
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        self.profile = PersonProfile(name=self.name)
        self._variants = self._generate_name_variants(name)
        
    def _generate_name_variants(self, name: str) -> List[str]:
        """Generate searchable variants of the given name."""
        parts = name.split()
        variants = [name]
        
        # Handle common patterns
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            variants.append(f"{last} {first}")
            variants.append(f"{first}.{last}")
            variants.append(f"{first}-{last}")
            variants.append(f"{first}_{last}")
            variants.append(f"{first}{last}")
            variants.append(f"{first} {last[0]}.")  # First + Last initial
            variants.append(f"{first[0]}. {last}")
            variants.append(f"{first[0]}{last}")
            
            # Quoted exact match for Google
            variants.append(f'"{first} {last}"')
            variants.append(f'"{last} {first}"')
            
        return list(set(variants))
    
    def _get_headers(self) -> Dict[str, str]:
        """Rotate user-agent and other headers."""
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
            'DNT': '1'
        }
    
    def _fetch_url(self, url: str, params: Optional[Dict] = None) -> Optional[str]:
        """Fetch a URL with proper headers and delay."""
        time.sleep(REQUEST_DELAY)
        try:
            resp = self.session.get(url, headers=self._get_headers(), 
                                    params=params, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                logger.warning(f"Rate-limited on {url}, backing off...")
                time.sleep(5)
                return None
            else:
                logger.debug(f"Non-200 status {resp.status_code} for {url}")
                return None
        except Exception as e:
            logger.debug(f"Request failed for {url}: {e}")
            return None

    # ---------- Search Modules ----------
    def search_google(self, query: str, num_results: int = 20) -> List[SearchResult]:
        """Perform a Google search using dorking techniques."""
        # Construct Google search URL with dork modifiers
        dork_queries = [
            f'"{query}" -site:gov -site:mil',  # Quote exact match
            f'intitle:"{query}"',
            f'inurl:"{query}"',
            f'"{query}" filetype:pdf',
            f'"{query}" site:linkedin.com',
            f'"{query}" site:twitter.com',
            f'"{query}" site:github.com',
        ]
        
        results = []
        for dork in dork_queries[:3]:  # Limit to avoid being too aggressive
            encoded = urllib.parse.quote(dork)
            url = f"https://www.google.com/search?q={encoded}&num={num_results}"
            html = self._fetch_url(url)
            if not html:
                continue
                
            # Simple regex-based result extraction (production would use BeautifulSoup)
            # This extracts titles and URLs from Google's HTML structure
            pattern = r'<a href="\/url\?q=(https?://[^&"]+)&[^"]*"[^>]*>(.*?)</a>'
            matches = re.findall(pattern, html, re.IGNORECASE)
            
            for match in matches[:num_results]:
                url_clean, title_raw = match
                title = re.sub(r'<[^>]+>', '', title_raw).strip()
                if title and url_clean:
                    results.append(SearchResult(
                        source="Google Dork",
                        title=title[:200],
                        url=url_clean,
                        snippet="",
                        confidence=0.8
                    ))
            break  # Only use first working dork to avoid duplicate heavy loads
            
        return results

    def search_social_media(self) -> Dict[str, List[str]]:
        """Search for profiles on major social platforms."""
        platforms = {
            'linkedin': f'https://www.linkedin.com/pub/dir/?first={self._variants[0].split()[0]}&last={self._variants[0].split()[-1]}',
            'twitter': f'https://twitter.com/search?q={urllib.parse.quote(self.name)}',
            'facebook': f'https://www.facebook.com/search/top?q={urllib.parse.quote(self.name)}',
            'github': f'https://github.com/search?q={urllib.parse.quote(self.name)}',
            'instagram': f'https://www.instagram.com/web/search/topsearch/?context=user&query={urllib.parse.quote(self.name)}',
            'reddit': f'https://www.reddit.com/search/?q={urllib.parse.quote(self.name)}',
        }
        
        results = {}
        for platform, url in platforms.items():
            # For APIs and public search pages, we don't do deep scraping
            # Instead, we generate search URLs for the user to manually check
            results[platform] = [url]
            logger.info(f"Generated {platform} search link: {url}")
            time.sleep(0.5)
            
        return results

    def search_public_records(self) -> List[SearchResult]:
        """Search for public records, news, and mentions."""
        queries = [
            f'"{self.name}" site:gov',
            f'"{self.name}" site:edu',
            f'"{self.name}" news',
            f'"{self.name}" "email"',
            f'"{self.name}" "phone"',
        ]
        results = []
        
        # Use a more conservative search method for records
        for query in queries[:2]:
            encoded = urllib.parse.quote(query)
            url = f"https://www.google.com/search?q={encoded}&num=10"
            html = self._fetch_url(url)
            if not html:
                continue
                
            # Extract potential record links
            pattern = r'<a href="\/url\?q=(https?://[^&"]+)&[^"]*"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html, re.IGNORECASE)
            
            for url_clean, title in matches[:5]:
                if any(domain in url_clean for domain in ['.gov', '.edu', 'archive.', 'news']):
                    results.append(SearchResult(
                        source="Public Record",
                        title=title.strip(),
                        url=url_clean,
                        snippet="",
                        confidence=0.75
                    ))
            time.sleep(1)
            
        return results

    # ---------- Intelligence Extraction ----------
    def extract_emails(self, text: str) -> Set[str]:
        """Extract email addresses from text."""
        pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        matches = re.findall(pattern, text)
        return set(matches)

    def extract_usernames(self, text: str) -> Set[str]:
        """Extract potential usernames (simple heuristic)."""
        # Look for @username patterns
        pattern = r'@([a-zA-Z0-9_]+)'
        matches = re.findall(pattern, text)
        return set(matches)

    def extract_social_links(self, text: str) -> Dict[str, str]:
        """Extract social media profile links from text."""
        platforms = {
            'linkedin': r'https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+',
            'twitter': r'https?://(?:www\.)?twitter\.com/[a-zA-Z0-9_]+',
            'github': r'https?://(?:www\.)?github\.com/[a-zA-Z0-9_-]+',
            'facebook': r'https?://(?:www\.)?facebook\.com/[a-zA-Z0-9.]+',
            'instagram': r'https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+',
        }
        
        found = {}
        for platform, pattern in platforms.items():
            matches = re.findall(pattern, text)
            if matches:
                found[platform] = matches[0]
        return found

    # ---------- Main Orchestration ----------
    def run(self) -> PersonProfile:
        """Execute the full OSINT dorking pipeline."""
        logger.info(f"Starting OSINT dorking for: {self.name}")
        
        # Step 1: Google Dorking with variants
        logger.info("Running Google dorks...")
        all_results = []
        for variant in self._variants[:3]:  # Limit variants for performance
            results = self.search_google(variant, num_results=15)
            all_results.extend(results)
            logger.info(f"Found {len(results)} results for '{variant}'")
        
        # Step 2: Social Media search links
        logger.info("Generating social media search links...")
        social_links = self.search_social_media()
        for platform, urls in social_links.items():
            self.profile.social_links[platform] = urls[0] if urls else ""
        
        # Step 3: Public records search
        logger.info("Searching public records...")
        records = self.search_public_records()
        self.profile.public_records = records
        
        # Step 4: Extract intelligence from all results
        combined_text = " ".join([r.title + " " + r.url for r in all_results])
        combined_text += " ".join([r.title + " " + r.url for r in records])
        
        self.profile.possible_emails = self.extract_emails(combined_text)
        self.profile.possible_usernames = self.extract_usernames(combined_text)
        self.profile.social_links.update(self.extract_social_links(combined_text))
        
        # Store raw results
        self.profile.raw_results = all_results
        self.profile.articles = [r for r in all_results if any(w in r.url for w in ['news', 'article', 'blog'])]
        
        logger.info(f"OSINT dorking complete for {self.name}")
        return self.profile

    def save_report(self, profile: PersonProfile, format: str = "json") -> Path:
        """Save the profile to a file."""
        data = {
            "name": profile.name,
            "emails": list(profile.possible_emails),
            "usernames": list(profile.possible_usernames),
            "social_links": profile.social_links,
            "public_records": [{"title": r.title, "url": r.url} for r in profile.public_records],
            "articles": [{"title": r.title, "url": r.url} for r in profile.articles],
            "raw_count": len(profile.raw_results)
        }
        
        filename = OUTPUT_DIR / f"{profile.name.replace(' ', '_')}_osint.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Report saved to {filename}")
        return filename

# ---------- CLI Entry Point ----------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="OSINT Dorking Suite — Find digital footprints by name")
    parser.add_argument("name", type=str, help="Full name of the person to search (e.g., 'John Doe')")
    parser.add_argument("--output", "-o", type=str, help="Output file path (default: ./osint_results/<name>_osint.json)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    engine = DorkEngine(args.name)
    profile = engine.run()
    report_path = engine.save_report(profile)
    
    print("\n" + "="*60)
    print(f"OSINT Dorking Complete for: {profile.name}")
    print("="*60)
    print(f"Emails found: {len(profile.possible_emails)}")
    if profile.possible_emails:
        for email in profile.possible_emails:
            print(f"  - {email}")
    print(f"Usernames found: {len(profile.possible_usernames)}")
    if profile.possible_usernames:
        for username in profile.possible_usernames:
            print(f"  - {username}")
    print(f"Social links found: {len(profile.social_links)}")
    for platform, url in profile.social_links.items():
        if url:
            print(f"  - {platform}: {url}")
    print(f"Public records: {len(profile.public_records)}")
    print(f"Total raw results: {len(profile.raw_results)}")
    print(f"\nFull report saved to: {report_path}")
    print("="*60)

if __name__ == "__main__":
    main()