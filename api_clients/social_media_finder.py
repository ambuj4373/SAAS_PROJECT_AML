"""
api_clients/social_media_finder.py — Enhanced Social Media Profile Detection

Uses intelligent scraping + pattern matching to find company/person social profiles
without needing API keys. Works with Tavily and direct website analysis.
"""

import re
from typing import Dict, List
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

# Common social media URL patterns
SOCIAL_PATTERNS = {
    "LinkedIn": [
        r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[\w-]+",
        r"linkedin\.com/(?:company|in)/[\w-]+",
    ],
    "Twitter / X": [
        r"https?://(?:www\.)?(twitter|x)\.com/[\w]+",
        r"(?:twitter|x)\.com/[\w]+",
    ],
    "Facebook": [
        r"https?://(?:www\.)?facebook\.com/[\w\.\-/]+",
        r"facebook\.com/[\w\.\-/]+",
    ],
    "Instagram": [
        r"https?://(?:www\.)?instagram\.com/[\w\.]+",
        r"instagram\.com/[\w\.]+",
    ],
    "YouTube": [
        r"https?://(?:www\.)?youtube\.com/(?:c|channel|user)/[\w\-]+",
        r"youtube\.com/(?:c|channel|user)/[\w\-]+",
    ],
    "TikTok": [
        r"https?://(?:www\.)?tiktok\.com/@[\w\.]+",
        r"tiktok\.com/@[\w\.]+",
    ],
}

# Advanced search queries for finding social profiles
SOCIAL_SEARCH_QUERIES = {
    "LinkedIn": [
        'site:linkedin.com "{company_name}" company',
        'site:linkedin.com/company "{company_name}"',
        '"{company_name}" linkedin company profile',
    ],
    "Twitter / X": [
        'site:twitter.com "{company_name}"',
        'site:x.com "{company_name}"',
        '"{company_name}" twitter official account',
    ],
    "Facebook": [
        'site:facebook.com "{company_name}" page',
        'site:facebook.com/pages "{company_name}"',
        '"{company_name}" facebook official page',
    ],
    "Instagram": [
        'site:instagram.com "{company_name}"',
        '"{company_name}" instagram official',
    ],
    "YouTube": [
        'site:youtube.com "{company_name}" channel',
        'site:youtube.com/c "{company_name}"',
    ],
}

# High-value profile indicators
PROFILE_INDICATORS = {
    "LinkedIn": [
        "company profile",
        "company page",
        "employees",
        "followers",
        "about",
    ],
    "Twitter / X": [
        "verified account",
        "@" + "company_handle",
        "official account",
    ],
    "Facebook": [
        "official page",
        "verified page",
        "followers",
    ],
}


def generate_search_name_variations(company_name: str) -> List[str]:
    """
    Generate multiple name variations for searching social profiles.
    
    Tries:
    1. Shortened name (remove legal suffixes like "PLC", "Ltd", "Inc")
    2. First word only (for compound names)
    3. Main keywords (remove stop words)
    4. Original full name (fallback)
    
    Example: "WISE PLC" → ["WISE", "WISE PLC"]
    Example: "Smith & Co Ltd" → ["Smith", "Smith & Co", "Smith & Co Ltd"]
    
    Args:
        company_name: Company legal name
    
    Returns:
        List of name variations, ordered by likelihood of finding profile
    """
    if not company_name:
        return []
    
    company_name = company_name.strip()
    variations = []
    
    # Legal suffixes to remove for shortened names
    legal_suffixes = [
        " PLC", " Ltd", " Limited", " Inc", " Incorporated",
        " LLC", " PLLC", " SA", " S.A.", " GmbH", " AG",
        " & Co", " and Co", " Corp", " Corporation", " Company",
        " (UK)", " UK", " Europe", " International", " Group",
    ]
    
    # Stop words to filter out
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "of", "to", "for",
        "is", "was", "are", "were", "be", "been", "being",
    }
    
    # 1. Try shortened name (remove legal suffixes)
    shortened = company_name
    for suffix in legal_suffixes:
        if shortened.lower().endswith(suffix.lower()):
            shortened = shortened[:-len(suffix)].strip()
            break
    
    if shortened and shortened != company_name:
        variations.append(shortened)
    
    # 2. Try first word only (if more than one word)
    words = company_name.split()
    if len(words) > 1:
        first_word = words[0]
        if first_word.lower() not in stop_words and first_word not in variations:
            variations.append(first_word)
    
    # 3. Try main keywords (remove stop words)
    keywords = [
        w for w in words
        if w.lower() not in stop_words and len(w) > 2
    ]
    if len(keywords) > 1 and len(keywords) < len(words):
        keywords_str = " ".join(keywords)
        if keywords_str not in variations:
            variations.append(keywords_str)
    
    # 4. Original full name (always include as fallback)
    if company_name not in variations:
        variations.append(company_name)
    
    return variations


def extract_social_links_from_html(html_content: str, company_name: str = "") -> Dict[str, List[str]]:
    """
    Extract social media links from HTML content using pattern matching.
    
    Args:
        html_content: HTML text to search
        company_name: Company name for context (improves matching)
    
    Returns:
        Dict with platform names and lists of found URLs
    """
    if not html_content:
        return {}
    
    found_links = {}
    
    for platform, patterns in SOCIAL_PATTERNS.items():
        links_for_platform = []
        
        for pattern in patterns:
            matches = re.finditer(pattern, html_content, re.IGNORECASE)
            for match in matches:
                url = match.group(0)
                # Clean up URL
                if not url.startswith("http"):
                    url = "https://" + url
                if url not in links_for_platform:
                    links_for_platform.append(url)
        
        if links_for_platform:
            found_links[platform] = links_for_platform
    
    return found_links


def scrape_website_for_social_links(website_url: str, max_pages: int = 5) -> Dict[str, str]:
    """
    Scrape company website for embedded social media links.
    
    Args:
        website_url: Company website URL
        max_pages: Max pages to scrape for links
    
    Returns:
        Dict of platform → URL (best match per platform)
    """
    if not website_url:
        return {}
    
    try:
        # Add protocol if missing
        if not website_url.startswith("http"):
            website_url = "https://" + website_url
        
        response = requests.get(website_url, timeout=10, verify=False)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Extract all links
        all_links = [a.get("href") for a in soup.find_all("a") if a.get("href")]
        
        # Convert relative to absolute URLs
        absolute_links = []
        for link in all_links:
            try:
                abs_link = urljoin(website_url, link)
                absolute_links.append(abs_link)
            except:
                pass
        
        # Combine into single text for pattern matching
        all_text = " ".join(absolute_links)
        
        # Extract social links
        social_links = extract_social_links_from_html(all_text)
        
        # Return best link per platform (first found = usually in header/footer = most official)
        result = {}
        for platform, links in social_links.items():
            if links:
                result[platform] = links[0]
        
        return result
    
    except Exception as e:
        return {}


def search_social_profiles_via_web(
    company_name: str,
    search_fn=None,  # Tavily search function
) -> Dict[str, str]:
    """
    Search for social media profiles using web search with smart name variations.
    
    Tries multiple name variations to find profiles:
    1. Shortened name (remove legal suffixes)
    2. First word only (short names)
    3. Main keywords (important words)
    4. Full legal name (fallback)
    
    Args:
        company_name: Legal name of company to search
        search_fn: Web search function (e.g., tavily_search)
    
    Returns:
        Dict of platform → URL (best match per platform)
    """
    if not search_fn or not company_name:
        return {}
    
    found_profiles = {}
    
    # Generate name variations ordered by likelihood
    name_variations = generate_search_name_variations(company_name)
    
    try:
        for platform in SOCIAL_SEARCH_QUERIES.keys():
            # Try each name variation for this platform
            for name_variation in name_variations:
                if platform in found_profiles:
                    break  # Already found good match for this platform
                
                # Try different query templates for this name variation
                for query_template in SOCIAL_SEARCH_QUERIES.get(platform, []):
                    if platform in found_profiles:
                        break
                    
                    # Substitute name variation
                    search_query = query_template.replace("{company_name}", name_variation)
                    
                    try:
                        # Execute search
                        results = search_fn(search_query)
                        
                        if not results:
                            continue
                        
                        # Parse results for social links
                        for result in results:
                            url = result.get("url", "")
                            # Check if URL matches this platform
                            if platform.lower() in url.lower() or platform.split()[0].lower() in url.lower():
                                # Score by profile indicators
                                snippet = (result.get("snippet", "") or "").lower()
                                is_profile = any(
                                    indicator in snippet 
                                    for indicator in PROFILE_INDICATORS.get(platform, [])
                                )
                                
                                # Accept if it's a profile-like URL
                                if is_profile or "linkedin.com/company" in url or "@" in url:
                                    found_profiles[platform] = url
                                    break  # Found best match, move to next platform
                    
                    except Exception:
                        continue  # Try next query template
        
        return found_profiles
    
    except Exception as e:
        return {}


def find_company_social_profiles(
    company_name: str,
    website_url: str = None,
    search_fn=None,
) -> Dict[str, Dict]:
    """
    Comprehensive social media profile finder.
    
    Tries multiple approaches:
    1. Scrape company website for social links
    2. Search web for official profiles
    3. Return structured results
    
    Args:
        company_name: Company name
        website_url: Company website (optional)
        search_fn: Web search function (optional)
    
    Returns:
        Dict with results and methodology
    """
    results = {
        "links": {},
        "sources": [],
        "confidence": "low",
    }
    
    # Step 1: Try website scraping
    if website_url:
        try:
            website_links = scrape_website_for_social_links(website_url)
            if website_links:
                results["links"].update(website_links)
                results["sources"].append("Website scrape (header/footer links)")
                results["confidence"] = "high"
        except:
            pass
    
    # Step 2: Try web search
    if search_fn:
        try:
            search_links = search_social_profiles_via_web(company_name, search_fn)
            if search_links:
                # Only add if not already found via website
                for platform, url in search_links.items():
                    if platform not in results["links"]:
                        results["links"][platform] = url
                        results["sources"].append(f"Web search (verified profile)")
                if results["confidence"] == "low":
                    results["confidence"] = "medium"
        except:
            pass
    
    return results


# Quick search fallback (for when no APIs available)
def generate_direct_search_urls(company_name: str) -> Dict[str, str]:
    """
    Generate direct search URLs for manual lookup using smartest name variation.
    
    Uses the shortest/simplest name variation for better search results:
    - "WISE PLC" → searches for "WISE" (much better results)
    - "Smith & Co Ltd" → searches for "Smith" (cleaner, more results)
    - Falls back to full name if no variation available
    
    Returns URLs that go straight to platform search (no API needed).
    """
    # Get name variations and use the shortest one (usually best for searches)
    name_variations = generate_search_name_variations(company_name)
    
    # Use shortest variation (typically best for platform searches)
    search_name = name_variations[0] if name_variations else company_name
    encoded_name = quote(search_name)
    
    return {
        "LinkedIn": f"https://www.linkedin.com/search/results/companies/?keywords={encoded_name}",
        "Twitter / X": f"https://x.com/search?q={encoded_name}%20verified",
        "Facebook": f"https://www.facebook.com/search/pages/?q={encoded_name}",
        "Instagram": f"https://www.instagram.com/web/search/topsearch/?query={encoded_name}",
        "YouTube": f"https://www.youtube.com/results?search_query={encoded_name}%20official%20channel",
    }
