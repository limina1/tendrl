#!/usr/bin/env python3
"""
Internet connectivity checker for nostr-feeds.

Checks if the system has internet connectivity before attempting
to fetch events from relays.
"""

import socket
import time
from typing import Tuple, Optional


class ConnectivityChecker:
    """
    Check internet connectivity with caching to avoid repeated checks.
    """

    def __init__(self, cache_ttl: int = 30):
        """
        Initialize connectivity checker.

        Args:
            cache_ttl: How long to cache connectivity results (seconds)
        """
        self.cache_ttl = cache_ttl
        self._last_check_time: Optional[float] = None
        self._last_check_result: bool = False

    def is_online(self, use_cache: bool = True) -> Tuple[bool, str]:
        """
        Check if system has internet connectivity.

        Args:
            use_cache: Use cached result if available

        Returns:
            Tuple of (is_online: bool, message: str)
        """
        # Check cache first
        if use_cache and self._last_check_time:
            elapsed = time.time() - self._last_check_time
            if elapsed < self.cache_ttl:
                msg = "cached" if self._last_check_result else "cached (offline)"
                return self._last_check_result, msg

        # Perform actual check
        is_online = self._check_connectivity()

        # Update cache
        self._last_check_time = time.time()
        self._last_check_result = is_online

        if is_online:
            return True, "online"
        else:
            return False, "offline - no internet connection"

    def _check_connectivity(self) -> bool:
        """
        Perform actual connectivity check.

        Tries to connect to multiple reliable DNS servers to verify connectivity.

        Returns:
            True if online, False if offline
        """
        # List of reliable DNS servers to check
        test_hosts = [
            ("8.8.8.8", 53),      # Google DNS
            ("1.1.1.1", 53),      # Cloudflare DNS
            ("208.67.222.222", 53) # OpenDNS
        ]

        for host, port in test_hosts:
            try:
                # Try to create a socket connection
                socket.setdefaulttimeout(2)  # 2 second timeout
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((host, port))
                sock.close()
                return True  # Successfully connected
            except (socket.error, socket.timeout):
                continue  # Try next host

        # All hosts failed
        return False

    def check_relay_connectivity(self, relay_url: str, timeout: int = 3) -> bool:
        """
        Check if a specific Nostr relay is reachable.

        Args:
            relay_url: Relay URL (e.g., "wss://relay.damus.io")
            timeout: Connection timeout in seconds

        Returns:
            True if relay is reachable, False otherwise
        """
        # Extract host and port from relay URL
        url = relay_url.replace("wss://", "").replace("ws://", "")

        # Remove path if present
        if "/" in url:
            url = url.split("/")[0]

        # Default WebSocket ports
        if ":" in url:
            host, port = url.rsplit(":", 1)
            port = int(port)
        else:
            host = url
            port = 443  # Default wss port

        try:
            socket.setdefaulttimeout(timeout)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            sock.close()
            return True
        except (socket.error, socket.timeout):
            return False

    def invalidate_cache(self):
        """Force next check to be fresh (not cached)."""
        self._last_check_time = None


# Global singleton instance
_connectivity_checker = ConnectivityChecker()


def is_online(use_cache: bool = True) -> Tuple[bool, str]:
    """
    Check if system has internet connectivity (convenience function).

    Args:
        use_cache: Use cached result if available

    Returns:
        Tuple of (is_online: bool, message: str)
    """
    return _connectivity_checker.is_online(use_cache=use_cache)


def check_relay(relay_url: str, timeout: int = 3) -> bool:
    """
    Check if a specific relay is reachable (convenience function).

    Args:
        relay_url: Relay URL
        timeout: Connection timeout in seconds

    Returns:
        True if reachable, False otherwise
    """
    return _connectivity_checker.check_relay_connectivity(relay_url, timeout)


def invalidate_cache():
    """Force next connectivity check to be fresh."""
    _connectivity_checker.invalidate_cache()
