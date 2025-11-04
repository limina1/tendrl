"""
Template rendering engine for nostr-feeds

Converts enriched event dictionaries to plain text with semantic markers
using Jinja2 templates. The output is editor-agnostic and can be parsed
by Emacs, Neovim, VSCode, etc.
"""

import os
import json
from typing import Dict, Any, Optional
from pathlib import Path

import subprocess

from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound

from .utils import format_relative_time


def format_sats(value: Optional[int]) -> str:
    """
    Format satoshi amounts for display.

    Args:
        value: Satoshi amount (can be None)

    Returns:
        Formatted string

    Examples:
        >>> format_sats(100_000_000)
        '1.00 BTC'
        >>> format_sats(21000)
        '21k'
        >>> format_sats(500)
        '500'
        >>> format_sats(None)
        '0'
    """
    if value is None:
        return '0'

    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} BTC"
    elif value >= 1000:
        return f"{value / 1000:.0f}k"
    else:
        return str(value)


def truncate_content(content: str, length: int = 80, end: str = '...') -> str:
    """
    Truncate content with ellipsis.

    Args:
        content: Text to truncate
        length: Maximum length
        end: String to append when truncated

    Returns:
        Truncated string

    Examples:
        >>> truncate_content("Hello world", 5)
        'Hello...'
        >>> truncate_content("Short", 10)
        'Short'
    """
    if len(content) <= length:
        return content
    return content[:length].rstrip() + end


def to_json(value: Any) -> str:
    """
    Convert value to JSON string (for METADATA markers).

    Args:
        value: Any JSON-serializable value

    Returns:
        JSON string
    """
    return json.dumps(value)


def to_npub(pubkey: str) -> str:
    """
    Convert hex pubkey to npub (Bech32 encoding) using nak CLI.

    Args:
        pubkey: Hex pubkey string

    Returns:
        npub1... string

    Examples:
        >>> to_npub("76c71aae3a491f1d9eec47cba17e229cda4113a0bbb6e6ae1776d7643e29cafa")
        'npub1wmstz4ew9j5ar3jg0x5rus3ce99lzpxqq649vuwtuyq2w8r3t'
    """
    try:
        # Use nak to encode pubkey to npub
        result = subprocess.run(
            ['nak', 'encode', 'npub', pubkey],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return pubkey  # Fallback to hex if nak fails
    except Exception:
        return pubkey  # Fallback to hex if conversion fails


def to_nevent(event_id: str, author: Optional[str] = None) -> str:
    """
    Convert hex event ID to nevent (NIP-19) - modern replacement for 'note'.

    Note: 'note' encoding is deprecated. Use nevent for all regular events.

    Args:
        event_id: 64-char hex event ID
        author: Optional hex pubkey (recommended for better discoverability)

    Returns:
        nevent1... bech32 encoded identifier

    Examples:
        >>> to_nevent("2e3d1a4f82127feb5494dc324b78faed23d2695fab6440b32b45d933c8df56a1")
        'nevent1qqszu0g6f7ppyllt2j2dcvjt0raw6g7jd906kezqkv45tkfner04dggshgqky'
        >>> to_nevent("2e3d1a4f...", author="5c508c34...")
        'nevent1qqszu0g6f7ppyllt2j2dcvjt0raw6g7jd906kezqkv45tkfner04dggzypw9prp...'
    """
    try:
        cmd = ['nak', 'encode', 'nevent']

        # Add author hint if provided
        if author:
            cmd.extend(['--author', author])

        # Event ID is positional
        cmd.append(event_id)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=2
        )
        return result.stdout.strip()
    except Exception:
        return event_id  # Fallback to hex if encoding fails


def to_naddr(event: Dict[str, Any]) -> str:
    """
    Convert addressable event (NIP-33) to naddr (NIP-19).

    Addressable events include:
    - kind 30040: Publication indices
    - kind 30041: Publication sections
    - kind 30023: Long-form articles
    - kind 30818: Wiki articles

    Args:
        event: Event dictionary with kind, pubkey, and tags

    Returns:
        naddr1... bech32 encoded identifier

    Examples:
        >>> event = {'kind': 30040, 'pubkey': '5c508c34...', 'tags': [['d', 'my-pub']]}
        >>> to_naddr(event)
        'naddr1qqxnzd3cxqmrzv3exgmr2wfeqgs9ay6qa7777..."
    """
    try:
        # Extract required fields
        kind = event.get('kind')
        pubkey = event.get('pubkey')

        # Extract d-tag
        d_tag = None
        for tag in event.get('tags', []):
            if tag and len(tag) >= 2 and tag[0] == 'd':
                d_tag = tag[1]
                break

        if not kind or not pubkey or d_tag is None:
            # If missing required fields, return fallback address format
            return f"{kind}:{pubkey}:{d_tag or 'unknown'}"

        # Build nak encode naddr command
        cmd = ['nak', 'encode', 'naddr', '--kind', str(kind), '--pubkey', pubkey, '--identifier', d_tag]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=2
        )
        return result.stdout.strip()
    except Exception:
        # Fallback to address format if encoding fails
        kind = event.get('kind', 'unknown')
        pubkey = event.get('pubkey', 'unknown')
        d_tag = 'unknown'
        for tag in event.get('tags', []):
            if tag and len(tag) >= 2 and tag[0] == 'd':
                d_tag = tag[1]
                break
        return f"{kind}:{pubkey}:{d_tag}"


def to_nostr_uri(identifier: str, truncate: int = 0) -> str:
    """
    Wrap bech32 identifier in nostr: URI scheme (NIP-21).

    Args:
        identifier: Already-encoded bech32 identifier (npub1.../nevent1.../etc.)
        truncate: If > 0, truncate identifier to this many chars (for display)

    Returns:
        nostr:identifier URI

    Examples:
        >>> to_nostr_uri("npub1wmstz4ew9j5ar...")
        'nostr:npub1wmstz4ew9j5ar...'
        >>> to_nostr_uri("nevent1qqszu0g6f...")
        'nostr:nevent1qqszu0g6f...'
        >>> to_nostr_uri("npub1wmstz...", truncate=16)
        'nostr:npub1wmstz...'
    """
    # If already has nostr: prefix, return as-is
    if identifier.startswith('nostr:'):
        return identifier

    uri = f"nostr:{identifier}"

    if truncate > 0 and len(identifier) > truncate:
        # Truncate but keep nostr: prefix
        return f"nostr:{identifier[:truncate]}..."

    return uri


def to_nevent_uri(event_id: str, author: Optional[str] = None) -> str:
    """
    Convert hex event ID to nostr:nevent URI (NIP-19 + NIP-21).

    This is a convenience filter that combines nevent encoding with nostr: URI wrapping.

    Args:
        event_id: 64-char hex event ID
        author: Optional hex pubkey for author hint

    Returns:
        nostr:nevent1... URI

    Examples:
        >>> to_nevent_uri("2e3d1a4f82127feb5494dc324b78faed23d2695fab6440b32b45d933c8df56a1")
        'nostr:nevent1qqszu0g6f7ppyllt2j2dcvjt0raw6g7jd906kezqkv45tkfner04dggshgqky'
    """
    try:
        nevent = to_nevent(event_id, author)
        return to_nostr_uri(nevent)
    except Exception as e:
        # Fallback to wrapped hex if encoding fails
        return f"nostr:{event_id}"


def to_npub_uri(pubkey: str) -> str:
    """
    Convert hex pubkey to nostr:npub URI (NIP-19 + NIP-21).

    This is a convenience filter that combines npub encoding with nostr: URI wrapping.

    Args:
        pubkey: 64-char hex pubkey

    Returns:
        nostr:npub1... URI

    Examples:
        >>> to_npub_uri("5c508c34f58866ec7341aaf10cc1af52e9232bb9f859c8103ca5ecf2aa93bf78")
        'nostr:npub1t3ggcd843pnwcu6p4tcsesd02t5jx2aelpvusypu5hk0925nhauqjjl5g4'
    """
    npub = to_npub(pubkey)
    return to_nostr_uri(npub)


def to_naddr_uri(event: Dict[str, Any]) -> str:
    """
    Convert addressable event to nostr:naddr URI (NIP-19 + NIP-21).

    This is a convenience filter that combines naddr encoding with nostr: URI wrapping.

    Args:
        event: Event dictionary with kind, pubkey, and d-tag

    Returns:
        nostr:naddr1... URI

    Examples:
        >>> event = {'kind': 30040, 'pubkey': '5c508c34...', 'tags': [['d', 'my-pub']]}
        >>> to_naddr_uri(event)
        'nostr:naddr1qqxnzd3cxqmrzv3exgmr2wfeqgs9ay6qa7777...'
    """
    naddr = to_naddr(event)
    return to_nostr_uri(naddr)


def truncate_npub(pubkey: str, length: int = 16) -> str:
    """
    Get truncated npub for display (e.g., npub1wmr3...g240).

    Args:
        pubkey: Hex pubkey string
        length: Total length including npub1 prefix and suffix

    Returns:
        Truncated npub with ellipsis

    Examples:
        >>> truncate_npub("76c71aae3a491f1d9eec47cba17e229cda4113a0bbb6e6ae1776d7643e29cafa", 16)
        'npub1wmr3...g240'
    """
    try:
        npub = to_npub(pubkey)
        if len(npub) <= length:
            return npub

        # Show first 8 chars (npub1xxx) and last 4 chars
        prefix_len = 8
        suffix_len = 4
        return f"{npub[:prefix_len]}...{npub[-suffix_len:]}"
    except Exception:
        # Fallback to hex truncation
        return f"{pubkey[:8]}...{pubkey[-4:]}"


def linkify_content_text(content: str) -> str:
    """
    Add semantic markers for hashtags, URLs, images, and nostr: URIs in content (plain text mode).

    Args:
        content: Plain text content

    Returns:
        Content with semantic markers for editor parsing

    Examples:
        >>> linkify_content_text("Check out #nostr at https://nostr.com")
        'Check out <<<HASHTAG:nostr>>>#nostr<<</HASHTAG>>> at <<<URL:https://nostr.com>>>https://nostr.com<<</URL>>>'
        >>> linkify_content_text("Image: https://example.com/photo.jpg")
        'Image: <<<IMAGE:https://example.com/photo.jpg>>>https://example.com/photo.jpg<<</IMAGE>>>'
    """
    import re

    # Skip content that's already inside markers (from render_nprofiles)
    def not_inside_markers(match):
        start = match.start()
        # Check if we're inside existing markers by looking back
        before = content[:start]
        open_markers = before.count('<<<')
        close_markers = before.count('>>>')
        # If unbalanced, we're inside a marker
        return open_markers == close_markers

    # Pattern for image URLs (common extensions)
    image_pattern = r'(https?://[^\s<]+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s<]*)?)'

    # Pattern for URLs (http/https)
    url_pattern = r'(https?://[^\s<]+)'

    # Pattern for hashtags
    hashtag_pattern = r'(#\w+)'

    # Pattern for existing nostr: URIs (nevent, npub, note, naddr) but NOT already marked
    # Skip nprofile as those are handled by render_nprofiles
    nostr_uri_pattern = r'nostr:(nevent|npub|note|naddr)[a-zA-Z0-9]+'

    # First, mark nostr URIs (but skip if inside markers)
    def replace_nostr_uri(m):
        if not_inside_markers(m) and not m.group(0).startswith('<<<'):
            return f'<<<NOSTR_URI:{m.group(0)}>>>{m.group(0)}<<</NOSTR_URI>>>'
        return m.group(0)

    content = re.sub(nostr_uri_pattern, replace_nostr_uri, content)

    # Then mark image URLs with IMAGE marker
    def replace_image(m):
        if not_inside_markers(m) and not m.group(1).startswith('<<<'):
            return f'<<<IMAGE:{m.group(1)}>>>{m.group(1)}<<</IMAGE>>>'
        return m.group(1)

    content = re.sub(image_pattern, replace_image, content, flags=re.IGNORECASE)

    # Then mark remaining URLs (excluding images already processed)
    def replace_url(m):
        if not_inside_markers(m) and not m.group(1).startswith('<<<'):
            # Skip if this is an image URL (already processed)
            if re.match(r'.*\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?.*)?$', m.group(1), re.IGNORECASE):
                return m.group(1)
            return f'<<<URL:{m.group(1)}>>>{m.group(1)}<<</URL>>>'
        return m.group(1)

    content = re.sub(url_pattern, replace_url, content)

    # Finally, mark hashtags
    def replace_hashtag(m):
        if not_inside_markers(m) and not m.group(0).startswith('<<<'):
            return f'<<<HASHTAG:{m.group(1)[1:]}>>>{m.group(1)}<<</HASHTAG>>>'
        return m.group(0)

    content = re.sub(hashtag_pattern, replace_hashtag, content)

    return content


def linkify_content_html(content: str) -> str:
    """
    Convert hashtags, URLs, and nostr: URIs to HTML links.
    Detects image URLs and renders them as thumbnails with expand capability.

    Args:
        content: Plain text content

    Returns:
        HTML-safe content with clickable links and image previews

    Examples:
        >>> linkify_content_html("Check out #nostr at https://nostr.com")
        'Check out <span class="hashtag">#nostr</span> at <a href="https://nostr.com">...</a>'
        >>> linkify_content_html("Image: https://example.com/photo.jpg")
        'Image: <div class="image-preview">...</div>'
    """
    import re
    from html import escape

    # Check if content already contains HTML (from render_nprofiles filter)
    # If so, skip escaping to preserve those links
    has_html_links = '<a' in content

    if not has_html_links:
        # Escape HTML first (only for plain text content)
        content = escape(content)

    # Pattern for image URLs (common extensions)
    image_pattern = r'(https?://[^\s<]+\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?[^\s<]*)?)'

    # Pattern for URLs (http/https) - will exclude images
    url_pattern = r'(https?://[^\s<]+)'

    # Pattern for hashtags
    hashtag_pattern = r'(#\w+)'

    # Pattern for existing nostr: URIs
    # Exclude npub and nprofile (handled by render_nprofiles filter)
    nostr_uri_pattern = r'(nostr:(nevent|note|naddr)[a-zA-Z0-9]+)'

    # Convert nostr URIs to clickable links
    content = re.sub(
        nostr_uri_pattern,
        r'<a href="\1" class="nostr-uri" data-uri="\1" target="_blank">\1</a>',
        content
    )

    # Convert image URLs to thumbnail previews with expand capability
    def replace_image(match):
        img_url = match.group(1)
        return f'''<div class="image-preview">
    <img src="{img_url}"
         alt="Image"
         loading="lazy"
         class="thumbnail"
         onclick="expandImage(this)"
         title="Click to expand">
    <a href="{img_url}" target="_blank" class="image-link" rel="noopener noreferrer">🔗 Open</a>
</div>'''

    content = re.sub(image_pattern, replace_image, content, flags=re.IGNORECASE)

    # Convert remaining URLs to clickable links (excluding images already processed)
    def replace_url(match):
        url = match.group(1)
        # Skip if this is an image URL (already processed)
        if re.match(r'.*\.(?:jpg|jpeg|png|gif|webp|bmp|svg)(?:\?.*)?$', url, re.IGNORECASE):
            return url
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'

    content = re.sub(url_pattern, replace_url, content)

    # Convert hashtags to spans
    content = re.sub(
        hashtag_pattern,
        r'<span class="hashtag">\1</span>',
        content
    )

    # Preserve line breaks
    content = content.replace('\n', '<br>\n')

    return content


def linkify_content(content: str, html: bool = False) -> str:
    """
    Add semantic markers or HTML links for hashtags, URLs, and nostr: URIs.

    Args:
        content: Plain text content
        html: If True, generate HTML links; if False, use semantic markers

    Returns:
        Content with semantic markers or HTML links

    Examples:
        >>> linkify_content("Check out #nostr")
        'Check out <<<HASHTAG:nostr>>>#nostr<<</HASHTAG>>>'
        >>> linkify_content("Check out #nostr", html=True)
        'Check out <span class="hashtag">#nostr</span>'
    """
    if html:
        return linkify_content_html(content)
    else:
        return linkify_content_text(content)


def render_nprofile_mentions(content: str, profile_cache, html: bool = False) -> str:
    """
    Replace nostr:nprofile URIs with profile names and links.

    Automatically fetches missing profiles from relays.

    Args:
        content: Content containing nostr:nprofile mentions
        profile_cache: ProfileCache instance for lookups and fetching
        html: If True, render as HTML; if False, use semantic markers

    Returns:
        Content with nprofile URIs replaced by profile names

    Examples:
        >>> render_nprofile_mentions("Check out nostr:nprofile1qqsr...", cache)
        'Check out <<<PROFILE:nostr:npub1abc...>>>@alice<<</PROFILE>>>'
    """
    import re
    from .config import nprofile_to_hex

    # Pattern for nostr:nprofile URIs
    nprofile_pattern = r'nostr:(nprofile1[a-zA-Z0-9]+)'

    def replace_nprofile(match):
        nprofile_uri = match.group(0)  # Full nostr:nprofile1...
        nprofile = match.group(1)      # Just nprofile1...

        try:
            # Decode nprofile to hex pubkey
            pubkey = nprofile_to_hex(nprofile)

            # Get profile from cache
            profile = profile_cache.get(pubkey)

            if not profile:
                # Try to fetch from relays if missing
                try:
                    profile_cache.fetch_and_store(pubkey)
                    profile = profile_cache.get(pubkey)
                except:
                    pass  # Fallback to placeholder below

            # Get profile name
            if profile:
                name = profile.get('display_name') or profile.get('name') or f"{pubkey[:8]}"
            else:
                name = f"{pubkey[:8]}"

            # Create npub URI for the link
            npub = to_npub(pubkey)
            npub_uri = f"nostr:{npub}"

            # Format with semantic marker or HTML
            if html:
                return f'<a href="{npub_uri}" class="profile-mention" data-pubkey="{pubkey}" title="{npub_uri}">@{name}</a>'
            else:
                return f'<<<PROFILE:{npub_uri}>>>@{name}<<</PROFILE>>>'

        except Exception as e:
            # If decode fails, return original
            return nprofile_uri

    # Replace all nprofile mentions
    return re.sub(nprofile_pattern, replace_nprofile, content)


class Renderer:
    """
    Jinja2-based template renderer with semantic markers.

    Takes enriched event dictionaries and renders them using
    templates from the configured template directory.
    """

    def __init__(self, template_dir: Optional[str] = None, html_mode: bool = False, profile_cache=None):
        """
        Initialize renderer with template directory.

        Args:
            template_dir: Path to templates directory
                         (defaults to ~/.config/nostr-feeds/templates)
            html_mode: If True, use HTML templates and filters; if False, use plain text
            profile_cache: Optional ProfileCache for rendering nprofile mentions

        Raises:
            FileNotFoundError: If template directory doesn't exist
        """
        if template_dir is None:
            template_dir = os.path.expanduser('~/.config/nostr-feeds/templates')

        template_path = Path(template_dir)

        if not template_path.exists():
            raise FileNotFoundError(f"Template directory not found: {template_dir}")

        self.template_dir = str(template_path)
        self.html_mode = html_mode
        self.profile_cache = profile_cache

        # Create Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(['html', 'xml']) if html_mode else False,
            trim_blocks=True,
            lstrip_blocks=True
        )

        # Register custom filters
        self.env.filters['format_sats'] = format_sats
        self.env.filters['format_relative_time'] = format_relative_time
        self.env.filters['truncate_content'] = truncate_content
        self.env.filters['to_json'] = to_json
        self.env.filters['to_npub'] = to_npub
        self.env.filters['to_nevent'] = to_nevent
        self.env.filters['to_naddr'] = to_naddr
        self.env.filters['to_nostr_uri'] = to_nostr_uri
        self.env.filters['to_nevent_uri'] = to_nevent_uri
        self.env.filters['to_npub_uri'] = to_npub_uri
        self.env.filters['to_naddr_uri'] = to_naddr_uri
        self.env.filters['truncate_npub'] = truncate_npub

        # Register linkify filter based on mode
        if html_mode:
            self.env.filters['linkify'] = linkify_content_html
        else:
            self.env.filters['linkify'] = linkify_content_text

        # Register nprofile mention filter (always available, no-op if no cache)
        if profile_cache:
            self.env.filters['render_nprofiles'] = lambda content: render_nprofile_mentions(content, profile_cache, html_mode)
        else:
            # No-op filter if profile_cache not available
            self.env.filters['render_nprofiles'] = lambda content: content

        # Register contextual rendering filter
        self.env.filters['render_preview'] = lambda enriched: self.render_referenced_event(enriched, context='preview')

    def render_event(
        self,
        enriched_event: Dict[str, Any],
        template_name: Optional[str] = None
    ) -> str:
        """
        Render single enriched event using template.

        Args:
            enriched_event: Enriched event dictionary from Enricher
            template_name: Template to use (defaults to 'short-note-card.j2')

        Returns:
            Rendered text with semantic markers

        Raises:
            TemplateNotFound: If template doesn't exist

        Examples:
            >>> renderer = Renderer()
            >>> enriched = {...}  # From enricher
            >>> rendered = renderer.render_event(enriched)
            >>> '<<<EVENT:' in rendered
            True
        """
        # Auto-select template based on event kind if not explicitly provided
        if template_name is None:
            event = enriched_event.get('event', {})
            template_name = self.select_template(event)

        template = self.env.get_template(template_name)

        return template.render(**enriched_event)

    def render_events(
        self,
        enriched_events: list[Dict[str, Any]],
        template_name: Optional[str] = None
    ) -> list[str]:
        """
        Render multiple enriched events.

        Args:
            enriched_events: List of enriched event dictionaries
            template_name: Template to use for all events

        Returns:
            List of rendered text strings

        Examples:
            >>> renderer = Renderer()
            >>> events = [enriched1, enriched2, enriched3]
            >>> rendered = renderer.render_events(events)
            >>> len(rendered)
            3
        """
        return [
            self.render_event(event, template_name)
            for event in enriched_events
        ]

    def render_stat_detail(
        self,
        stat_type: str,
        stat_data: Dict[str, Any],
        event_id: str
    ) -> str:
        """
        Render expanded stat details.

        Args:
            stat_type: Type of stat ('zaps', 'reactions', 'replies')
            stat_data: Detailed stat data
            event_id: Event ID this stat belongs to

        Returns:
            Rendered stat detail text

        Examples:
            >>> renderer = Renderer()
            >>> stat_data = {'count': 15, 'total_sats': 21000, 'details': [...]}
            >>> rendered = renderer.render_stat_detail('zaps', stat_data, 'abc123')
        """
        template_name = f"{stat_type}-detail.j2"

        try:
            template = self.env.get_template(template_name)
            return template.render(
                stat_type=stat_type,
                event_id=event_id,
                **stat_data
            )
        except TemplateNotFound:
            # Fallback: simple text representation
            return f"{stat_type.title()}: {stat_data.get('count', 0)} items"

    def select_template(
        self,
        event: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
        context: str = 'full'
    ) -> str:
        """
        Select appropriate template based on event kind, content type, and context.

        This implements the template selection logic from TEMPLATING-COMPARISON.org
        with contextual rendering support (preview vs. full).

        Args:
            event: Event dictionary
            config: Feed configuration (optional)
            context: Rendering context - 'preview' (embedded), 'full' (standalone), 'focus' (detailed)

        Returns:
            Template name to use

        Examples:
            >>> renderer = Renderer()
            >>> event = {'kind': 1, ...}
            >>> renderer.select_template(event)
            'short-note-card.j2'
            >>> event = {'kind': 30023, ...}
            >>> renderer.select_template(event, context='preview')
            'article-preview.j2'
            >>> renderer.select_template(event, context='full')
            'article-full.j2'
        """
        kind = event.get('kind', 1)

        # Check config for explicit template override
        if config and 'template' in config:
            return config['template']

        # Check event tags for content-type
        tags = event.get('tags', [])
        for tag in tags:
            if len(tag) >= 2 and tag[0] == 'content-type':
                content_type = tag[1]

                if content_type == 'text/markdown':
                    return 'markdown-article.j2'
                elif content_type == 'text/x-org':
                    return 'org-article.j2'

        # HTML mode - use html/ subdirectory templates
        if self.html_mode:
            # Contextual template selection based on (kind, context)
            if kind == 30023:  # Long-form articles (NIP-23)
                if context == 'preview':
                    return self._check_template_exists('html/article-preview.j2', 'html/short-note-card.j2')
                else:
                    return self._check_template_exists('html/article-full.j2', 'html/short-note-card.j2')

            # Standard kind-based template selection
            template_map = {
                1: 'html/short-note-card.j2',
                3: 'html/contact-list.j2',
                6: 'html/repost-card.j2',
                7: 'html/reaction-card.j2',
                9735: 'html/zap-card.j2',
                9802: 'html/highlight-card.j2'
            }

            template_name = template_map.get(kind)
            if template_name:
                return self._check_template_exists(template_name, 'html/short-note-card.j2')

            return 'html/short-note-card.j2'

        # Plain text mode - original logic
        # Contextual template selection based on (kind, context)
        # For articles and long-form content, context matters
        if kind == 30023:  # Long-form articles (NIP-23)
            if context == 'preview':
                return self._check_template_exists('article-preview.j2', 'generic-event-card.j2')
            else:  # 'full' or 'focus'
                return self._check_template_exists('article-full.j2', 'generic-event-card.j2')

        elif kind == 30818:  # Wiki articles
            if context == 'preview':
                return self._check_template_exists('wiki-preview.j2', 'generic-event-card.j2')
            else:
                return self._check_template_exists('wiki-page.j2', 'generic-event-card.j2')

        elif kind == 30040:  # Channels
            if context == 'preview':
                return self._check_template_exists('channel-preview.j2', 'generic-event-card.j2')
            else:
                return self._check_template_exists('channel-message.j2', 'generic-event-card.j2')

        # Standard kind-based template selection (no preview/full distinction)
        template_map = {
            1: 'short-note-card.j2',      # Text note
            3: 'contact-list.j2',          # Contact list
            6: 'repost-card.j2',           # Repost
            7: 'reaction-card.j2',         # Reaction
            9735: 'zap-card.j2',           # Zap
            9802: 'highlight-card.j2'      # Highlight
        }

        template_name = template_map.get(kind)

        if template_name:
            return self._check_template_exists(template_name, 'generic-event-card.j2')

        # Fallback to generic event card for unknown kinds
        return 'generic-event-card.j2'

    def _check_template_exists(self, template_name: str, fallback: str) -> str:
        """
        Check if template exists, return fallback if not.

        Args:
            template_name: Preferred template name
            fallback: Fallback template name

        Returns:
            Template name that exists
        """
        try:
            self.env.get_template(template_name)
            return template_name
        except TemplateNotFound:
            return fallback

    def render_referenced_event(
        self,
        enriched_event: Dict[str, Any],
        context: str = 'preview'
    ) -> str:
        """
        Render a referenced event with appropriate template based on context.

        This is a convenience method for rendering events that are embedded
        within other events (e.g., highlighted articles, reposted content).

        Args:
            enriched_event: Enriched event dictionary
            context: Rendering context ('preview' for embedded, 'full' for standalone)

        Returns:
            Rendered text with semantic markers

        Examples:
            >>> renderer = Renderer()
            >>> article = {...}  # kind 30023 event
            >>> preview = renderer.render_referenced_event(article, context='preview')
            >>> '<<<ARTICLE_PREVIEW:' in preview
            True
        """
        event = enriched_event.get('event', {})
        template_name = self.select_template(event, context=context)
        return self.render_event(enriched_event, template_name)

    def render_html_feed(
        self,
        enriched_events: list[Dict[str, Any]],
        feed_name: str = "Nostr Feed"
    ) -> str:
        """
        Render a complete HTML page with multiple events.

        Only works in HTML mode. Wraps events in html/feed.j2 template with
        base layout, CSS, and JavaScript.

        Args:
            enriched_events: List of enriched event dictionaries
            feed_name: Name to display in page header

        Returns:
            Complete HTML document as string

        Raises:
            ValueError: If not in HTML mode

        Examples:
            >>> renderer = Renderer(html_mode=True)
            >>> events = [enriched1, enriched2, enriched3]
            >>> html = renderer.render_html_feed(events, "My Timeline")
        """
        if not self.html_mode:
            raise ValueError("render_html_feed() requires html_mode=True")

        try:
            template = self.env.get_template('html/feed.j2')
            return template.render(
                events=enriched_events,
                feed_name=feed_name
            )
        except TemplateNotFound:
            raise FileNotFoundError("html/feed.j2 template not found")


def create_renderer(template_dir: Optional[str] = None) -> Renderer:
    """
    Factory function to create renderer.

    Args:
        template_dir: Optional template directory path

    Returns:
        Configured Renderer instance

    Examples:
        >>> renderer = create_renderer()
        >>> renderer = create_renderer('~/custom/templates')
    """
    return Renderer(template_dir)
