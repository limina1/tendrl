"""
Configuration loader for nostr-feeds enricher

Load and parse TOML feed configuration files.
"""

import os
import toml
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


def npub_to_hex(npub: str) -> str:
    """
    Convert npub (bech32) to hex pubkey using nak CLI.

    Args:
        npub: npub1... string or hex pubkey

    Returns:
        Hex pubkey string

    Raises:
        RuntimeError: If nak decode fails

    Examples:
        >>> npub_to_hex('npub1abc...')
        'deadbeef...'
        >>> npub_to_hex('deadbeef...')  # Already hex, return as-is
        'deadbeef...'
    """
    # If already hex (64 chars, all hex), return as-is
    if len(npub) == 64 and all(c in '0123456789abcdef' for c in npub.lower()):
        return npub.lower()

    # If not npub format, assume invalid
    if not npub.startswith('npub1'):
        raise ValueError(f"Invalid npub or hex pubkey: {npub}")

    # Use nak decode to convert
    try:
        result = subprocess.run(
            ['nak', 'decode', npub],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        hex_key = result.stdout.strip()

        # Validate hex output
        if len(hex_key) != 64 or not all(c in '0123456789abcdef' for c in hex_key.lower()):
            raise RuntimeError(f"nak decode returned invalid hex: {hex_key}")

        return hex_key.lower()

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nak decode failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("nak decode timed out")
    except FileNotFoundError:
        raise RuntimeError("nak CLI not found. Install from: https://github.com/fiatjaf/nak")


def nprofile_to_hex(nprofile: str) -> str:
    """
    Convert nprofile (bech32) to hex pubkey using nak CLI.

    nprofile encodes a pubkey plus optional relay hints.
    We extract just the pubkey.

    Args:
        nprofile: nprofile1... string (NIP-19)

    Returns:
        Hex pubkey string

    Raises:
        RuntimeError: If nak decode fails
        ValueError: If invalid nprofile format

    Examples:
        >>> nprofile_to_hex('nprofile1qqsrhuxx...')
        'deadbeef...'
    """
    import json

    if not nprofile.startswith('nprofile1'):
        raise ValueError(f"Invalid nprofile format: {nprofile}")

    # Use nak decode to convert
    try:
        result = subprocess.run(
            ['nak', 'decode', nprofile],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )

        # nak decode outputs JSON for nprofiles: {"pubkey": "...", "relays": [...]}
        output = result.stdout.strip()
        data = json.loads(output)
        hex_key = data.get('pubkey', '')

        # Validate hex output
        if len(hex_key) != 64 or not all(c in '0123456789abcdef' for c in hex_key.lower()):
            raise RuntimeError(f"nak decode returned invalid hex: {hex_key}")

        return hex_key.lower()

    except json.JSONDecodeError as e:
        raise RuntimeError(f"nak decode returned invalid JSON: {e}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nak decode nprofile failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("nak decode timed out")
    except FileNotFoundError:
        raise RuntimeError("nak CLI not found. Install from: https://github.com/fiatjaf/nak")


class Config:
    """
    Feed configuration loaded from TOML.

    Provides access to global settings, feed definitions,
    and dependency trees.
    """

    def __init__(self, config_dict: Dict[str, Any]):
        """
        Initialize config from parsed TOML.

        Args:
            config_dict: Dictionary from toml.load()
        """
        self.raw = config_dict
        self.global_config = config_dict.get('global', {})
        self.feeds = config_dict.get('feed', {})
        self.users = config_dict.get('users', {})

    @classmethod
    def load(cls, config_path: str) -> 'Config':
        """
        Load configuration from TOML file.

        Args:
            config_path: Path to config.toml file

        Returns:
            Config instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            toml.TomlDecodeError: If TOML is invalid

        Examples:
            >>> config = Config.load('~/.config/nostr-feeds/config.toml')
            >>> config.get_feed('timeline')
            {'name': 'timeline', 'type': 'follows', ...}
        """
        path = Path(os.path.expanduser(config_path))

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, 'r') as f:
            config_dict = toml.load(f)

        return cls(config_dict)

    def get_db_dir(self) -> str:
        """
        Get database directory from config.

        Returns:
            Expanded database directory path

        Examples:
            >>> config.get_db_dir()
            '/home/user/.local/share/nostr-feeds'
        """
        db_dir = self.global_config.get('db_dir', '~/.local/share/nostr-feeds')
        return os.path.expanduser(db_dir)

    def get_template_dir(self) -> str:
        """
        Get template directory from config.

        Returns:
            Expanded template directory path
        """
        template_dir = self.global_config.get('template_dir', '~/.config/nostr-feeds/templates')
        return os.path.expanduser(template_dir)

    def get_profiles_db(self) -> str:
        """
        Get profiles database filename.

        Returns:
            Profiles database filename (relative to db_dir)
        """
        return self.global_config.get('profile_cache_db', 'profiles.db')

    def get_profile_cache_ttl(self) -> int:
        """
        Get profile cache TTL in seconds.

        Returns:
            TTL in seconds (default: 3600 = 1 hour)

        Examples:
            >>> config.get_profile_cache_ttl()
            3600
        """
        return self.global_config.get('profile_cache_ttl', 3600)

    def get_event_refresh_interval(self) -> int:
        """
        Get interval for refreshing event stats (reactions/replies/zaps).

        Returns:
            Refresh interval in seconds (default: 300 = 5 minutes)

        Examples:
            >>> config.get_event_refresh_interval()
            300
        """
        return self.global_config.get('event_refresh_interval', 300)

    def get_feed(self, feed_name: str) -> Optional[Dict[str, Any]]:
        """
        Get feed configuration by name.

        Args:
            feed_name: Feed name (e.g., 'timeline', 'replies')

        Returns:
            Feed config dictionary or None if not found

        Examples:
            >>> feed = config.get_feed('timeline')
            >>> feed['db_file']
            'timeline.db'
        """
        return self.feeds.get(feed_name)

    def get_feed_db_path(self, feed_name: str) -> str:
        """
        Get full path to feed database.

        Args:
            feed_name: Feed name

        Returns:
            Full path to feed's SQLite database

        Examples:
            >>> config.get_feed_db_path('timeline')
            '/home/user/.local/share/nostr-feeds/timeline.db'
        """
        feed = self.get_feed(feed_name)
        if not feed:
            raise ValueError(f"Feed not found: {feed_name}")

        db_file = feed.get('db_file')
        if not db_file:
            raise ValueError(f"Feed '{feed_name}' has no db_file configured")

        db_dir = self.get_db_dir()
        return os.path.join(db_dir, db_file)

    def get_feed_root_config(self, feed_name: str) -> Optional[Dict[str, Any]]:
        """
        Get root event configuration for feed.

        Args:
            feed_name: Feed name

        Returns:
            Root config with kind, template, filter, deps

        Examples:
            >>> root = config.get_feed_root_config('timeline')
            >>> root['kind']
            1
            >>> root['template']
            'short-note-card.j2'
        """
        feed = self.get_feed(feed_name)
        if not feed:
            return None

        return feed.get('root', {})

    def get_dependency_tree(self, feed_name: str) -> Dict[str, Any]:
        """
        Get dependency tree for feed root events.

        Args:
            feed_name: Feed name

        Returns:
            Dictionary of dependencies: {dep_name: dep_config}

        Examples:
            >>> deps = config.get_dependency_tree('timeline')
            >>> deps['author']
            {'kind': 0, 'relation': 'author', 'required': True}
            >>> deps['reactions']
            {'kind': 7, 'relation': 'e_tag', 'mode': 'aggregate', ...}
        """
        root = self.get_feed_root_config(feed_name)
        if not root:
            return {}

        return root.get('deps', {})

    def get_refresh_interval(self, feed_name: str) -> int:
        """
        Get refresh interval for feed (in seconds).

        Args:
            feed_name: Feed name

        Returns:
            Refresh interval in seconds (0 = no auto-refresh)

        Examples:
            >>> config.get_refresh_interval('timeline')
            15
        """
        feed = self.get_feed(feed_name)
        if not feed:
            return 0

        return feed.get('refresh_interval', 0)

    def get_fetch_strategy(self, feed_name: str) -> str:
        """
        Get fetch_strategy for feed.

        Args:
            feed_name: Feed name

        Returns:
            Fetch strategy: 'stream' | 'periodic' | 'on_load' | 'manual'
            Defaults to 'periodic' if not specified

        Examples:
            >>> config.get_fetch_strategy('timeline')
            'stream'
        """
        feed = self.get_feed(feed_name)
        if not feed:
            return 'periodic'

        return feed.get('fetch_strategy', 'periodic')

    def get_relay_mode(self, feed_name: str) -> str:
        """
        Get relay_mode for feed.

        Args:
            feed_name: Feed name

        Returns:
            Relay mode: 'general' | 'inbox' | 'outbox'
            Defaults to 'general' if not specified

        Examples:
            >>> config.get_relay_mode('timeline')
            'outbox'
        """
        feed = self.get_feed(feed_name)
        if not feed:
            return 'general'

        return feed.get('relay_mode', 'general')

    def list_feeds(self) -> list[str]:
        """
        List all configured feed names.

        Returns:
            List of feed names

        Examples:
            >>> config.list_feeds()
            ['timeline', 'replies', 'thread', 'global']
        """
        return list(self.feeds.keys())

    def get_default_npub(self) -> Optional[str]:
        """
        Get default npub from config, converted to hex.

        Returns:
            Hex pubkey or None

        Examples:
            >>> config.get_default_npub()
            'dc4cd086cd7ce5b1832adf4fdd1211289880d2c7e295bcb0e684c01acee77c06'
        """
        npub = self.global_config.get('default_npub')
        if npub:
            return npub_to_hex(npub)
        return None

    def get_known_npubs(self) -> list[str]:
        """
        Get list of known npubs from config.

        Returns:
            List of npub strings

        Examples:
            >>> config.get_known_npubs()
            ['npub1abc...', 'npub1def...']
        """
        return self.global_config.get('known_npubs', [])

    def get_user_config(self, npub: str) -> Optional[Dict[str, Any]]:
        """
        Get user-specific configuration.

        Args:
            npub: User's npub

        Returns:
            User config dict or None

        Examples:
            >>> config.get_user_config('npub1abc...')
            {'name': 'alice', 'relays': [...]}
        """
        return self.users.get(npub)

    def get_default_relays(self) -> list[str]:
        """
        Get default relays from user config.

        Returns:
            List of relay URLs from default user config, or fallback relays

        Examples:
            >>> config.get_default_relays()
            ['wss://relay.damus.io', 'wss://relay.nostr.band']
        """
        # Try to get relays from default user config
        default_npub = self.global_config.get('default_npub')
        if default_npub:
            user_config = self.get_user_config(default_npub)
            if user_config and 'relays' in user_config:
                return user_config['relays']

        # Fallback to hardcoded relays
        return ['wss://relay.damus.io', 'wss://nos.lol', 'wss://relay.nostr.band']

    def __repr__(self) -> str:
        """String representation."""
        feed_names = self.list_feeds()
        return f"Config(feeds={feed_names}, db_dir={self.get_db_dir()})"


def create_default_config() -> str:
    """
    Create default configuration file content.

    Returns:
        TOML string with default configuration

    Examples:
        >>> toml_content = create_default_config()
        >>> print(toml_content)
        [global]
        db_dir = "~/.local/share/nostr-feeds"
        ...
    """
    return """[global]
db_dir = "~/.local/share/nostr-feeds"
template_dir = "~/.config/nostr-feeds/templates"
profile_cache_db = "profiles.db"

# Cache TTL settings (in seconds)
profile_cache_ttl = 3600  # Refresh profiles after 1 hour
event_refresh_interval = 300  # Refresh event stats (reactions/replies/zaps) after 5 minutes

# Default user (optional - used for follow-based feeds)
# default_npub = "npub1..."

# Known users (optional - for quick switching)
# known_npubs = ["npub1abc...", "npub1def..."]

# ============================================================================
# User Configuration (optional - for follow lists and relays)
# ============================================================================

# [users."npub1abc..."]
# name = "alice"
# relays = ["wss://relay.damus.io", "wss://nos.lol"]

# [users."npub1def..."]
# name = "bob"
# relays = ["wss://relay.damus.io"]

# ============================================================================
# Timeline Feed: Top-level posts from follows
# ============================================================================
[feed.timeline]
name = "timeline"
type = "follows"
db_file = "timeline.db"
refresh_interval = 15
filter_by_follows = true  # Only show posts from followed users

[feed.timeline.root]
kind = 1
template = "short-note-card.j2"

[feed.timeline.root.filter]
no_e_tags = true  # Only top-level posts

[feed.timeline.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.timeline.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.timeline.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]
expandable = true

[feed.timeline.root.deps.zaps]
kind = 9735
relation = "p_tag"
mode = "aggregate"
stats = ["count", "total_sats"]
expandable = true

# ============================================================================
# Global Feed: All posts from relay (no follow filter)
# ============================================================================
[feed.global]
name = "global"
type = "relay"
db_file = "global.db"
refresh_interval = 30
filter_by_follows = false  # Show all posts

[feed.global.root]
kind = 1
template = "short-note-card.j2"

[feed.global.root.filter]
no_e_tags = true

[feed.global.root.deps.author]
kind = 0
relation = "author"
required = true

[feed.global.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]
"""


def save_default_config(config_path: str) -> None:
    """
    Save default configuration to file.

    Args:
        config_path: Path to save config.toml

    Examples:
        >>> save_default_config('~/.config/nostr-feeds/config.toml')
    """
    path = Path(os.path.expanduser(config_path))
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w') as f:
        f.write(create_default_config())
