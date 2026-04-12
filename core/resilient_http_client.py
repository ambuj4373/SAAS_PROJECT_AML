"""
resilient_http_client.py

Implements resilient HTTP client with:
- Exponential backoff retry (3x: 1s, 2s, 4s)
- Timeout handling (15s default)
- Response caching (24h TTL)
- Circuit breaker pattern
- Comprehensive logging
"""

import logging
import time
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable
import requests
from functools import wraps
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker state machine."""
    CLOSED = "CLOSED"          # Normal operation
    OPEN = "OPEN"              # Failing, reject requests
    HALF_OPEN = "HALF_OPEN"    # Testing if service recovered


class SimpleCache:
    """
    Simple in-memory cache with TTL support.
    """
    
    def __init__(self, default_ttl_seconds: int = 86400):  # 24h default
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl_seconds
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value if exists and not expired."""
        if key not in self.cache:
            return None
        
        entry = self.cache[key]
        if datetime.now() > entry['expires_at']:
            del self.cache[key]
            return None
        
        return entry['value']
    
    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        """Cache value with optional TTL."""
        ttl = ttl_seconds or self.default_ttl
        self.cache[key] = {
            'value': value,
            'expires_at': datetime.now() + timedelta(seconds=ttl),
            'set_at': datetime.now(),
        }
    
    def clear(self):
        """Clear all cache."""
        self.cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'total_entries': len(self.cache),
            'oldest_entry': min((e['set_at'] for e in self.cache.values()), default=None),
            'newest_entry': max((e['set_at'] for e in self.cache.values()), default=None),
        }


class CircuitBreaker:
    """
    Circuit breaker for handling repeated failures.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: int = 60,
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
    
    def record_success(self):
        """Record a successful request."""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("✓ Circuit breaker CLOSED (service recovered)")
    
    def record_failure(self):
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.warning(f"⚠️  Circuit breaker OPEN (after {self.failure_count} failures)")
    
    def call_allowed(self) -> bool:
        """Check if request should be allowed."""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            # Check if timeout has passed
            if self.last_failure_time and \
               (datetime.now() - self.last_failure_time).total_seconds() > self.timeout_seconds:
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                logger.info("⚡ Circuit breaker HALF_OPEN (testing recovery)")
                return True
            return False
        
        # HALF_OPEN: allow request
        return True


class ResilientHTTPClient:
    """
    Resilient HTTP client with retry, timeout, caching, and circuit breaker.
    
    Features:
    - Exponential backoff: 1s, 2s, 4s retry delays
    - Configurable timeout (default 15s)
    - Response caching with TTL
    - Circuit breaker for failing endpoints
    - Comprehensive logging and metrics
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: int = 15,
        backoff_factor: float = 2.0,
        cache_ttl_seconds: int = 86400,  # 24 hours
    ):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.backoff_factor = backoff_factor
        
        # Cache for responses
        self.cache = SimpleCache(default_ttl_seconds=cache_ttl_seconds)
        
        # Circuit breakers per endpoint
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        
        # Metrics
        self.metrics = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'cache_hits': 0,
            'retries_used': 0,
            'timeouts': 0,
        }
    
    def _get_circuit_breaker(self, endpoint: str) -> CircuitBreaker:
        """Get or create circuit breaker for endpoint."""
        if endpoint not in self.circuit_breakers:
            self.circuit_breakers[endpoint] = CircuitBreaker()
        return self.circuit_breakers[endpoint]
    
    def _make_cache_key(self, method: str, url: str, **kwargs) -> str:
        """Generate cache key from request parameters."""
        key_data = f"{method}:{url}:{json.dumps(kwargs, sort_keys=True, default=str)}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def get(
        self,
        url: str,
        use_cache: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Resilient GET request with retry and caching.
        
        Args:
            url: URL to request
            use_cache: Use response cache
            **kwargs: Additional arguments to pass to requests.get()
        
        Returns:
            {
                'success': bool,
                'status_code': int,
                'data': dict or None,
                'error': str or None,
                'cached': bool,
                'retries_used': int,
                'timeout_seconds': int,
            }
        """
        self.metrics['total_requests'] += 1
        cache_key = self._make_cache_key('GET', url, **kwargs)
        
        # Try cache first
        if use_cache:
            cached_response = self.cache.get(cache_key)
            if cached_response:
                self.metrics['cache_hits'] += 1
                logger.debug(f"✓ Cache HIT for {url}")
                return {
                    **cached_response,
                    'cached': True,
                }
        
        # Check circuit breaker
        endpoint = url.split('/')[2]  # Extract host
        cb = self._get_circuit_breaker(endpoint)
        
        if not cb.call_allowed():
            self.metrics['failed_requests'] += 1
            logger.warning(f"❌ Circuit breaker OPEN, rejecting request to {endpoint}")
            return {
                'success': False,
                'status_code': 503,
                'data': None,
                'error': 'Circuit breaker open - service unavailable',
                'cached': False,
                'retries_used': 0,
                'timeout_seconds': self.timeout_seconds,
            }
        
        # Perform request with retries
        retries_used = 0
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                # Set timeout
                kwargs['timeout'] = kwargs.get('timeout', self.timeout_seconds)
                
                logger.info(f"📤 Request {attempt + 1}/{self.max_retries + 1}: GET {url}")
                response = requests.get(url, **kwargs)
                
                # Log response
                logger.debug(f"   Status: {response.status_code}")
                
                if response.status_code == 200:
                    # Success
                    response_data = {
                        'success': True,
                        'status_code': response.status_code,
                        'data': response.json() if response.text else None,
                        'error': None,
                        'cached': False,
                        'retries_used': retries_used,
                        'timeout_seconds': self.timeout_seconds,
                    }
                    
                    # Cache successful response
                    if use_cache:
                        self.cache.set(cache_key, response_data)
                    
                    self.metrics['successful_requests'] += 1
                    cb.record_success()
                    logger.info(f"✓ Request successful (attempt {attempt + 1})")
                    return response_data
                
                elif response.status_code >= 500:
                    # Server error - retry
                    last_error = f"Server error {response.status_code}"
                    logger.warning(f"⚠️  Server error {response.status_code}, retrying...")
                
                elif response.status_code >= 400:
                    # Client error - don't retry
                    self.metrics['failed_requests'] += 1
                    cb.record_failure()
                    logger.error(f"❌ Client error {response.status_code}: {response.text}")
                    return {
                        'success': False,
                        'status_code': response.status_code,
                        'data': None,
                        'error': f"HTTP {response.status_code}",
                        'cached': False,
                        'retries_used': retries_used,
                        'timeout_seconds': self.timeout_seconds,
                    }
                
            except requests.Timeout:
                self.metrics['timeouts'] += 1
                last_error = "Request timeout"
                logger.warning(f"⏱️  Timeout after {self.timeout_seconds}s, retrying...")
                
            except requests.ConnectionError as e:
                last_error = str(e)
                logger.warning(f"🔌 Connection error: {last_error}, retrying...")
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"❌ Unexpected error: {last_error}")
                break
            
            # Calculate backoff delay
            if attempt < self.max_retries:
                delay = (self.backoff_factor ** attempt)
                retries_used += 1
                logger.info(f"⏳ Waiting {delay}s before retry {attempt + 2}...")
                time.sleep(delay)
        
        # All retries exhausted
        self.metrics['failed_requests'] += 1
        cb.record_failure()
        logger.error(f"❌ Request failed after {self.max_retries + 1} attempts: {last_error}")
        
        return {
            'success': False,
            'status_code': None,
            'data': None,
            'error': last_error,
            'cached': False,
            'retries_used': retries_used,
            'timeout_seconds': self.timeout_seconds,
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get client metrics."""
        cache_stats = self.cache.get_stats()
        return {
            **self.metrics,
            'cache_stats': cache_stats,
            'success_rate': self.metrics['successful_requests'] / max(1, self.metrics['total_requests']),
            'cache_hit_rate': self.metrics['cache_hits'] / max(1, self.metrics['total_requests']),
        }
    
    def clear_cache(self):
        """Clear response cache."""
        self.cache.clear()
        logger.info("✓ Response cache cleared")


# Global default client
_default_client: Optional[ResilientHTTPClient] = None


def get_default_client() -> ResilientHTTPClient:
    """Get or create default resilient HTTP client."""
    global _default_client
    if _default_client is None:
        _default_client = ResilientHTTPClient(
            max_retries=3,
            timeout_seconds=15,
            backoff_factor=2.0,
            cache_ttl_seconds=86400,  # 24h
        )
        logger.info("✓ Created default ResilientHTTPClient")
    return _default_client


# Example usage / testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("\n" + "=" * 100)
    print("Testing ResilientHTTPClient...")
    print("=" * 100)
    
    client = ResilientHTTPClient(
        max_retries=3,
        timeout_seconds=10,
        backoff_factor=2.0,
    )
    
    # Test 1: Successful request
    print("\n1️⃣  Testing successful request...")
    response = client.get("https://jsonplaceholder.typicode.com/posts/1")
    print(f"   Status: {response['success']}")
    print(f"   Data keys: {list(response['data'].keys()) if response['data'] else None}")
    
    # Test 2: Cache hit
    print("\n2️⃣  Testing cache hit (same request)...")
    response2 = client.get("https://jsonplaceholder.typicode.com/posts/1")
    print(f"   Status: {response2['success']}")
    print(f"   Cached: {response2['cached']}")
    
    # Test 3: Metrics
    print("\n3️⃣  Metrics:")
    metrics = client.get_metrics()
    print(f"   Total requests: {metrics['total_requests']}")
    print(f"   Success rate: {metrics['success_rate']:.1%}")
    print(f"   Cache hits: {metrics['cache_hits']}")
    print(f"   Timeouts: {metrics['timeouts']}")
    
    print("\n" + "=" * 100)
