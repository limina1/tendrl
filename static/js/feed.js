/**
 * Tendrl Feed Client-Side Interactivity
 *
 * Handles user interactions with enriched notes:
 * - Load more events
 * - Real-time updates via SSE
 * - Note actions (react, zap, repost)
 */

class TendrlFeed {
  constructor(feedName) {
    this.feedName = feedName;
    this.limit = 50;
    this.offset = 0;
    this.loading = false;

    this.init();
  }

  init() {
    // Setup load more button
    const loadMoreBtn = document.querySelector('.load-more-btn');
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', () => this.loadMore());
    }

    // Setup note actions
    this.setupNoteActions();

    // Setup real-time updates (if enabled)
    if (window.TENDRL_REALTIME) {
      this.setupRealtimeUpdates();
    }
  }

  async loadMore() {
    if (this.loading) return;

    this.loading = true;
    const loadMoreBtn = document.querySelector('.load-more-btn');
    if (loadMoreBtn) {
      loadMoreBtn.textContent = 'Loading...';
      loadMoreBtn.disabled = true;
    }

    try {
      this.offset += this.limit;
      const response = await fetch(
        `/api/feed/${this.feedName}?limit=${this.limit}&offset=${this.offset}`
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      // Append new notes to feed
      const feedContent = document.querySelector('.feed-content');
      data.events.forEach(event => {
        const noteHtml = this.renderNote(event);
        feedContent.insertAdjacentHTML('beforeend', noteHtml);
      });

      // Re-setup actions for new notes
      this.setupNoteActions();

    } catch (error) {
      console.error('Failed to load more:', error);
      alert('Failed to load more notes');
    } finally {
      this.loading = false;
      if (loadMoreBtn) {
        loadMoreBtn.textContent = 'Load More';
        loadMoreBtn.disabled = false;
      }
    }
  }

  setupNoteActions() {
    // Reply action
    document.querySelectorAll('.note-action-reply').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const noteId = e.target.closest('.note').dataset.noteId;
        this.handleReply(noteId);
      });
    });

    // Repost action
    document.querySelectorAll('.note-action-repost').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const noteId = e.target.closest('.note').dataset.noteId;
        this.handleRepost(noteId);
      });
    });

    // React action
    document.querySelectorAll('.note-action-react').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const noteId = e.target.closest('.note').dataset.noteId;
        this.handleReact(noteId);
      });
    });

    // Zap action
    document.querySelectorAll('.note-action-zap').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const noteId = e.target.closest('.note').dataset.noteId;
        this.handleZap(noteId);
      });
    });

    // Reaction tag hover - show users
    document.querySelectorAll('.reaction-tag').forEach(tag => {
      tag.addEventListener('mouseenter', (e) => {
        const users = JSON.parse(tag.dataset.users || '[]');
        this.showUserTooltip(e.target, users);
      });
    });
  }

  handleReply(noteId) {
    console.log('Reply to:', noteId);
    // TODO: Open reply composer
    alert(`Reply to ${noteId} (not implemented)`);
  }

  handleRepost(noteId) {
    console.log('Repost:', noteId);
    // TODO: Publish repost event
    alert(`Repost ${noteId} (not implemented)`);
  }

  handleReact(noteId) {
    console.log('React to:', noteId);
    // TODO: Show emoji picker
    const emoji = prompt('Enter emoji reaction:', '❤️');
    if (emoji) {
      this.publishReaction(noteId, emoji);
    }
  }

  handleZap(noteId) {
    console.log('Zap:', noteId);
    // TODO: Show zap amount picker
    const amount = prompt('Enter sats amount:', '21');
    if (amount) {
      this.publishZap(noteId, parseInt(amount));
    }
  }

  async publishReaction(noteId, emoji) {
    // TODO: Integrate with Nostr signing
    console.log(`Publishing reaction: ${emoji} to ${noteId}`);
    alert('Reaction publishing not implemented yet');
  }

  async publishZap(noteId, amount) {
    // TODO: Integrate with Lightning wallet
    console.log(`Publishing zap: ${amount} sats to ${noteId}`);
    alert('Zap publishing not implemented yet');
  }

  showUserTooltip(element, users) {
    // TODO: Show nice tooltip with user profiles
    console.log('Users who reacted:', users);
  }

  setupRealtimeUpdates() {
    console.log('Setting up real-time updates via SSE...');

    const eventSource = new EventSource(`/stream/${this.feedName}`);

    eventSource.onmessage = (e) => {
      const event = JSON.parse(e.data);
      console.log('New event:', event);

      // Prepend new note to feed
      const feedContent = document.querySelector('.feed-content');
      const noteHtml = this.renderNote(event);
      feedContent.insertAdjacentHTML('afterbegin', noteHtml);

      // Re-setup actions
      this.setupNoteActions();

      // Show notification
      this.showNewEventNotification(event);
    };

    eventSource.onerror = (error) => {
      console.error('SSE error:', error);
      eventSource.close();
    };
  }

  renderNote(event) {
    // This would need to render the note.html.j2 template client-side
    // For now, we rely on server-side rendering
    // In production, use a client-side template engine or fetch pre-rendered HTML
    return `<!-- Note ${event.id} (client-side rendering not implemented) -->`;
  }

  showNewEventNotification(event) {
    // Show toast notification
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('New note', {
        body: event.content.substring(0, 100),
        icon: '/static/icon.png'
      });
    }
  }
}

// Initialize feed when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  const feedContainer = document.querySelector('.feed-container');
  if (feedContainer) {
    const feedName = new URLSearchParams(window.location.search).get('feed') || 'notes_enriched';
    window.tendrlFeed = new TendrlFeed(feedName);
  }
});

// Request notification permission
if ('Notification' in window && Notification.permission === 'default') {
  Notification.requestPermission();
}
