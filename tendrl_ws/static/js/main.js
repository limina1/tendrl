/**
 * Tendrl Publication Viewer - Interactive Features
 * Inspired by Alexandria's UI patterns
 */

// ============================================================================
// Event Viewer Modal
// ============================================================================

let currentEvent = null;

function showEventModal(eventJson) {
    currentEvent = typeof eventJson === 'string' ? JSON.parse(eventJson) : eventJson;
    const modal = document.getElementById('event-modal');
    const jsonPre = document.getElementById('event-json');

    jsonPre.textContent = JSON.stringify(currentEvent, null, 2);
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closeEventModal() {
    const modal = document.getElementById('event-modal');
    modal.classList.add('hidden');
    document.body.style.overflow = '';
    currentEvent = null;
}

function copyEventJson() {
    if (!currentEvent) return;
    navigator.clipboard.writeText(JSON.stringify(currentEvent, null, 2))
        .then(() => showToast('JSON copied to clipboard', 'success'))
        .catch(() => showToast('Failed to copy', 'error'));
}

function copyEventId() {
    if (!currentEvent || !currentEvent.id) return;
    navigator.clipboard.writeText(currentEvent.id)
        .then(() => showToast('Event ID copied', 'success'))
        .catch(() => showToast('Failed to copy', 'error'));
}

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeEventModal();
        hideContextMenu();
    }
});

// ============================================================================
// Toast Notifications
// ============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    // Auto-remove after 3 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 200);
    }, 3000);
}

// ============================================================================
// Context Menu
// ============================================================================

let activeContextMenu = null;

function showContextMenu(event, menuItems) {
    event.preventDefault();
    hideContextMenu();

    const menu = document.createElement('div');
    menu.className = 'context-menu';
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;

    menuItems.forEach(item => {
        if (item.divider) {
            const divider = document.createElement('div');
            divider.className = 'context-menu-divider';
            menu.appendChild(divider);
        } else {
            const menuItem = document.createElement('div');
            menuItem.className = 'context-menu-item';
            menuItem.innerHTML = `
                ${item.icon ? `<span class="icon">${item.icon}</span>` : ''}
                <span>${item.label}</span>
            `;
            menuItem.onclick = () => {
                item.action();
                hideContextMenu();
            };
            menu.appendChild(menuItem);
        }
    });

    document.body.appendChild(menu);
    activeContextMenu = menu;

    // Adjust position if menu goes off-screen
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        menu.style.left = `${window.innerWidth - rect.width - 10}px`;
    }
    if (rect.bottom > window.innerHeight) {
        menu.style.top = `${window.innerHeight - rect.height - 10}px`;
    }
}

function hideContextMenu() {
    if (activeContextMenu) {
        activeContextMenu.remove();
        activeContextMenu = null;
    }
}

// Hide context menu on click outside
document.addEventListener('click', hideContextMenu);

// ============================================================================
// Publication Card Context Menu
// ============================================================================

function setupPublicationCardContextMenu(card, eventData) {
    card.addEventListener('contextmenu', (e) => {
        showContextMenu(e, [
            {
                label: 'View Publication',
                icon: '📖',
                action: () => window.location.href = `/publication/${eventData.naddr || eventData.id}`
            },
            {
                label: 'Copy naddr',
                icon: '📋',
                action: () => {
                    if (eventData.naddr) {
                        navigator.clipboard.writeText(eventData.naddr)
                            .then(() => showToast('naddr copied', 'success'));
                    }
                }
            },
            {
                label: 'Copy Event ID',
                icon: '🔗',
                action: () => {
                    navigator.clipboard.writeText(eventData.id)
                        .then(() => showToast('Event ID copied', 'success'));
                }
            },
            { divider: true },
            {
                label: 'View Event JSON',
                icon: '{ }',
                action: () => showEventModal(eventData)
            },
            {
                label: 'Open on njump.me',
                icon: '🌐',
                action: () => window.open(`https://njump.me/${eventData.naddr || eventData.id}`, '_blank')
            }
        ]);
    });
}

// ============================================================================
// Table of Contents Navigation
// ============================================================================

function initTocNavigation() {
    const tocLinks = document.querySelectorAll('.toc-link');
    const headings = document.querySelectorAll('.content-main h1, .content-main h2, .content-main h3');

    if (tocLinks.length === 0 || headings.length === 0) return;

    // Click handling
    tocLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('href').slice(1);
            const target = document.getElementById(targetId);
            if (target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });

    // Scroll spy
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const id = entry.target.id;
                tocLinks.forEach(link => {
                    link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
                });
            }
        });
    }, { rootMargin: '-20% 0px -70% 0px' });

    headings.forEach(heading => {
        if (heading.id) {
            observer.observe(heading);
        }
    });
}

// ============================================================================
// Mobile TOC Toggle
// ============================================================================

function initMobileTocToggle() {
    const tocToggle = document.getElementById('toc-toggle');
    const tocSidebar = document.querySelector('.toc-sidebar');

    if (!tocToggle || !tocSidebar) return;

    tocToggle.addEventListener('click', () => {
        tocSidebar.classList.toggle('open');
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (!tocSidebar.contains(e.target) && !tocToggle.contains(e.target)) {
            tocSidebar.classList.remove('open');
        }
    });
}

// ============================================================================
// Nostr URI Handling
// ============================================================================

function copyNostrUri(type, value) {
    let uri;
    switch (type) {
        case 'npub':
            uri = `nostr:${value}`;
            break;
        case 'note':
            uri = `nostr:${value}`;
            break;
        case 'naddr':
            uri = `nostr:${value}`;
            break;
        default:
            uri = value;
    }
    navigator.clipboard.writeText(uri)
        .then(() => showToast('Nostr URI copied', 'success'))
        .catch(() => showToast('Failed to copy', 'error'));
}

// ============================================================================
// Theme Toggle (Dark/Light mode with localStorage persistence)
// ============================================================================

function initThemeToggle() {
    const themeToggle = document.getElementById('theme-toggle');
    if (!themeToggle) return;

    // Apply saved theme or system preference on load
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.documentElement.classList.add('dark');
        document.documentElement.classList.remove('light');
    } else if (savedTheme === 'light') {
        document.documentElement.classList.add('light');
        document.documentElement.classList.remove('dark');
    }
    // If no saved theme, CSS will use prefers-color-scheme

    // Update icon based on current theme
    updateThemeIcon(themeToggle);

    themeToggle.addEventListener('click', () => {
        const isDark = document.documentElement.classList.contains('dark') ||
                       (!document.documentElement.classList.contains('light') &&
                        window.matchMedia('(prefers-color-scheme: dark)').matches);

        if (isDark) {
            // Switch to light
            document.documentElement.classList.remove('dark');
            document.documentElement.classList.add('light');
            localStorage.setItem('theme', 'light');
            showToast('Light mode enabled', 'info');
        } else {
            // Switch to dark
            document.documentElement.classList.remove('light');
            document.documentElement.classList.add('dark');
            localStorage.setItem('theme', 'dark');
            showToast('Dark mode enabled', 'info');
        }

        updateThemeIcon(themeToggle);
    });
}

function updateThemeIcon(button) {
    const isDark = document.documentElement.classList.contains('dark') ||
                   (!document.documentElement.classList.contains('light') &&
                    window.matchMedia('(prefers-color-scheme: dark)').matches);

    // Moon icon for dark mode (clicking will switch to light)
    // Sun icon for light mode (clicking will switch to dark)
    button.innerHTML = isDark
        ? `<svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
             <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                   d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
           </svg>`
        : `<svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
             <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                   d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
           </svg>`;
}

// ============================================================================
// Publication Tree Navigation (for kind 30040 index events)
// ============================================================================

function renderPublicationTree(sections, container) {
    const ul = document.createElement('ul');
    ul.className = 'toc-list';

    sections.forEach(section => {
        const li = document.createElement('li');
        li.className = 'toc-item';

        const link = document.createElement('a');
        link.className = `toc-link depth-${section.depth || 1}`;
        link.href = `#section-${section.id || section.index}`;
        link.textContent = section.title;

        li.appendChild(link);

        if (section.children && section.children.length > 0) {
            const childUl = renderPublicationTree(section.children, li);
            li.appendChild(childUl);
        }

        ul.appendChild(li);
    });

    return ul;
}

// ============================================================================
// Lazy Loading for Feed
// ============================================================================

let feedPage = 1;
let feedLoading = false;
let feedHasMore = true;

function initInfiniteScroll() {
    const sentinel = document.getElementById('feed-sentinel');
    if (!sentinel) return;

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && !feedLoading && feedHasMore) {
            loadMoreFeed();
        }
    }, { rootMargin: '200px' });

    observer.observe(sentinel);
}

async function loadMoreFeed() {
    feedLoading = true;
    feedPage++;

    try {
        const response = await fetch(`/api/feed?page=${feedPage}`);
        const data = await response.json();

        if (data.publications && data.publications.length > 0) {
            const grid = document.querySelector('.feed-grid');
            data.publications.forEach(pub => {
                grid.insertAdjacentHTML('beforeend', renderPublicationCard(pub));
            });

            // Setup context menus for new cards
            const newCards = grid.querySelectorAll('.publication-card:not([data-menu-init])');
            newCards.forEach(card => {
                const eventData = JSON.parse(card.dataset.event);
                setupPublicationCardContextMenu(card, eventData);
                card.dataset.menuInit = 'true';
            });
        }

        feedHasMore = data.hasMore !== false;
    } catch (err) {
        console.error('Failed to load more feed:', err);
        showToast('Failed to load more publications', 'error');
    }

    feedLoading = false;
}

function renderPublicationCard(pub) {
    const eventJson = JSON.stringify(pub.event).replace(/'/g, "\\'").replace(/"/g, '&quot;');
    return `
        <div class="card publication-card"
             data-event="${eventJson}"
             onclick="window.location.href='/publication/${pub.naddr || pub.event.id}'">
            <div class="card-body">
                <div class="title">${escapeHtml(pub.title || 'Untitled')}</div>
                <div class="author">${escapeHtml(pub.author || 'Unknown')}</div>
                <div class="meta">
                    <span class="kind-badge kind-${pub.event.kind}">Kind ${pub.event.kind}</span>
                    <span>${formatDate(pub.event.created_at)}</span>
                </div>
            </div>
        </div>
    `;
}

// ============================================================================
// Utility Functions
// ============================================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(timestamp) {
    const date = new Date(timestamp * 1000);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

function truncate(str, maxLength) {
    if (!str) return '';
    return str.length > maxLength ? str.slice(0, maxLength) + '...' : str;
}

// ============================================================================
// Initialize on DOM Ready
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    initTocNavigation();
    initMobileTocToggle();
    initThemeToggle();
    initInfiniteScroll();

    // Setup context menus for existing publication cards
    document.querySelectorAll('.publication-card[data-event]').forEach(card => {
        try {
            const eventData = JSON.parse(card.dataset.event);
            setupPublicationCardContextMenu(card, eventData);
            card.dataset.menuInit = 'true';
        } catch (e) {
            console.warn('Failed to parse event data for card:', e);
        }
    });
});
