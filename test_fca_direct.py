#!/usr/bin/env python3
"""Test direct FCA Register access patterns."""

import requests
from bs4 import BeautifulSoup

# Try different endpoints
urls_to_test = [
    # Direct firm page (if we know the ID)
    "https://register.fca.org.uk/ShPo_PublicRegister/ViewFirm/718612",
    "https://register.fca.org.uk/FirmSearchResults",
    "https://register.fca.org.uk",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

print("Testing FCA Register endpoints:\n")

for url in urls_to_test:
    print(f"Testing: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"  Status: {response.status_code}")
        print(f"  Content-Type: {response.headers.get('content-type', 'N/A')}")
        
        # Check if it's HTML
        if 'html' in response.headers.get('content-type', '').lower():
            soup = BeautifulSoup(response.text, "html.parser")
            title = soup.find("title")
            print(f"  Title: {title.get_text() if title else 'N/A'}")
        
        print()
    except Exception as e:
        print(f"  Error: {e}\n")

# Try with session for cookies
print("\n" + "="*60)
print("Testing with requests.Session():\n")

session = requests.Session()

try:
    # Get main page first
    print("1. Getting register.fca.org.uk main page...")
    main = session.get("https://register.fca.org.uk/", timeout=10, headers=headers)
    print(f"   Status: {main.status_code}")
    
    # Now try search
    print("\n2. Attempting search with CH number...")
    search_url = "https://register.fca.org.uk/ShPo_PublicRegister/Search"
    params = {
        "ftc": "a",
        "sb": "01925556",
        "pg": 1,
    }
    result = session.get(search_url, params=params, timeout=10, headers=headers)
    print(f"   Status: {result.status_code}")
    
    if result.status_code == 200:
        soup = BeautifulSoup(result.text, "html.parser")
        print(f"   Found {len(soup.find_all('a'))} links")
        # Print some links
        for link in soup.find_all("a", href=True)[:3]:
            print(f"     - {link.get('href')}")
    
except Exception as e:
    print(f"   Error: {e}")
