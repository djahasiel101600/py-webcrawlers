#!/usr/bin/env python3
import requests
from requests_html import HTMLSession
import time
from urllib.parse import urljoin, urlparse
import json

class SimpleAndroidCrawler:
    def __init__(self):
        self.session = HTMLSession()
        self.visited = set()
        
    def crawl(self, url, max_pages=10):
        """Simple crawler with JavaScript support"""
        if url in self.visited or len(self.visited) >= max_pages:
            return
        
        print(f"Crawling: {url}")
        self.visited.add(url)
        
        try:
            # Get page with JavaScript rendering
            response = self.session.get(url)
            response.html.render(timeout=20, sleep=2)
            
            # Extract data
            title = response.html.find('title', first=True)
            title_text = title.text if title else "No title"
            
            print(f"Title: {title_text}")
            print(f"URL: {response.url}")
            print("-" * 50)
            
            # Find and follow links
            links = response.html.absolute_links
            for link in list(links)[:5]:  # Limit to 5 links per page
                if self.is_valid_url(link) and len(self.visited) < max_pages:
                    time.sleep(1)  Be polite
                    self.crawl(link, max_pages)
                    
        except Exception as e:
            print(f"Error: {e}")
    
    def is_valid_url(self, url):
        """Validate URL"""
        parsed = urlparse(url)
        return (parsed.scheme in ['http', 'https'] and 
                parsed.netloc and 
                url not in self.visited)

# Usage
if __name__ == "__main__":
    crawler = SimpleAndroidCrawler()
    start_url = input("Enter URL to crawl: ").strip() or "https://example.com"
    crawler.crawl(start_url, max_pages=10)
    
    print(f"\nCrawled {len(crawler.visited)} pages:")
    for url in crawler.visited:
        print(f" - {url}")