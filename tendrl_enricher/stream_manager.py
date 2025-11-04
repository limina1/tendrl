"""
Stream manager for nostr-feeds enricher

Manages persistent nak --stream processes for real-time event updates.
"""

import subprocess
import json
import threading
import time
import sys
import queue
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass

from .db import DatabaseManager, store_event


@dataclass
class StreamStats:
    """Statistics for a stream process."""
    events_received: int = 0
    errors: int = 0
    restarts: int = 0
    last_event_time: float = 0.0
    started_at: float = 0.0


class StreamProcess:
    """
    Manages a single nak --stream process.

    Handles:
    - Process lifecycle (start, stop, restart)
    - Line-by-line JSONL parsing
    - Auto-reconnect on disconnect
    - Buffering and backpressure
    """

    def __init__(
        self,
        stream_id: str,
        command: List[str],
        callback: Callable[[Dict], None],
        reconnect: bool = True,
        buffer_size: int = 100,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize stream process.

        Args:
            stream_id: Unique identifier for this stream
            command: nak command to execute (e.g., ['nak', 'req', '-k', '1', '--stream', ...])
            callback: Function to call for each received event
            reconnect: Auto-reconnect on disconnect
            buffer_size: Max events to buffer before blocking
            log_callback: Optional logging function
        """
        self.stream_id = stream_id
        self.command = command
        self.callback = callback
        self.reconnect = reconnect
        self.buffer_size = buffer_size
        self._log = log_callback or self._default_log

        self.process = None
        self.thread = None
        self.stop_event = threading.Event()
        self.stats = StreamStats()

        # Event buffer for backpressure
        self.buffer = queue.Queue(maxsize=buffer_size)
        self.processor_thread = None

    def start(self):
        """Start the stream process and processor."""
        if self.thread and self.thread.is_alive():
            self._log(f"[{self.stream_id}] Already running")
            return

        self.stats.started_at = time.time()
        self.stop_event.clear()

        # Start reader thread (reads from nak stdout)
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"stream-{self.stream_id}")
        self.thread.start()

        # Start processor thread (processes buffered events)
        self.processor_thread = threading.Thread(target=self._process_buffer, daemon=True, name=f"proc-{self.stream_id}")
        self.processor_thread.start()

        self._log(f"[{self.stream_id}] Started")

    def _run(self):
        """Main loop with reconnect logic."""
        while not self.stop_event.is_set():
            try:
                self._log(f"[{self.stream_id}] Starting nak process...")

                self.process = subprocess.Popen(
                    self.command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1  # Line buffered
                )

                self._log(f"[{self.stream_id}] Connected (PID {self.process.pid})")

                # Read events line by line
                for line in iter(self.process.stdout.readline, ''):
                    if self.stop_event.is_set():
                        break

                    if line.strip():
                        try:
                            event = json.loads(line)
                            # Add to buffer (blocks if buffer full)
                            self.buffer.put(event, timeout=5)
                            self.stats.events_received += 1
                            self.stats.last_event_time = time.time()

                        except json.JSONDecodeError as e:
                            self.stats.errors += 1
                            self._log(f"[{self.stream_id}] JSON decode error: {e}")
                        except queue.Full:
                            self.stats.errors += 1
                            self._log(f"[{self.stream_id}] Buffer full, dropping event")

                # Process ended
                returncode = self.process.wait()
                self._log(f"[{self.stream_id}] Process exited (code {returncode})")

                if not self.reconnect or self.stop_event.is_set():
                    break

                # Reconnect after delay
                self.stats.restarts += 1
                self._log(f"[{self.stream_id}] Reconnecting in 5s...")
                time.sleep(5)

            except Exception as e:
                self.stats.errors += 1
                self._log(f"[{self.stream_id}] Error: {e}")
                if not self.reconnect or self.stop_event.is_set():
                    break
                time.sleep(5)

        self._log(f"[{self.stream_id}] Stopped")

    def _process_buffer(self):
        """Process events from buffer."""
        while not self.stop_event.is_set():
            try:
                event = self.buffer.get(timeout=1)
                self.callback(event)
                self.buffer.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.stats.errors += 1
                self._log(f"[{self.stream_id}] Callback error: {e}")

    def stop(self, timeout: int = 5):
        """
        Stop the stream process.

        Args:
            timeout: Seconds to wait for graceful shutdown
        """
        self._log(f"[{self.stream_id}] Stopping...")
        self.stop_event.set()

        # Terminate nak process
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._log(f"[{self.stream_id}] Force killing...")
                self.process.kill()
                self.process.wait()

        # Wait for threads
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)

        if self.processor_thread and self.processor_thread.is_alive():
            self.processor_thread.join(timeout=timeout)

        self._log(f"[{self.stream_id}] Stopped")

    def is_alive(self) -> bool:
        """Check if stream is running."""
        return self.thread is not None and self.thread.is_alive()

    def get_stats(self) -> StreamStats:
        """Get stream statistics."""
        return self.stats

    def _default_log(self, message: str):
        """Default logging to stderr."""
        print(f"[stream] {message}", file=sys.stderr, flush=True)


class StreamManager:
    """
    Manages multiple stream processes.

    Handles:
    - Starting/stopping streams per feed
    - Connection grouping (multiple authors per relay)
    - Event storage to databases
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize stream manager.

        Args:
            db_manager: Database manager for storing events
            log_callback: Optional logging function
        """
        self.db_manager = db_manager
        self._log = log_callback or self._default_log

        # feed_name -> list of StreamProcess
        self.streams: Dict[str, List[StreamProcess]] = {}

    def start_feed_stream(
        self,
        feed_name: str,
        feed_config: Dict[str, Any],
        relay_resolution: Any  # RelayResolution
    ):
        """
        Start streaming for a feed.

        Args:
            feed_name: Feed name
            feed_config: Feed configuration dict
            relay_resolution: RelayResolution from RelayResolver
        """
        if feed_name in self.streams:
            self._log(f"Feed '{feed_name}' already streaming")
            return

        self._log(f"Starting streams for '{feed_name}'...")

        reconnect = feed_config.get('stream_reconnect', True)
        buffer_size = feed_config.get('stream_buffer_size', 100)

        if relay_resolution.strategy == 'broadcast':
            # Single process for all relays
            stream_id = f"{feed_name}:broadcast"
            cmd = self._build_command(feed_config, relay_resolution.relays)

            process = StreamProcess(
                stream_id,
                cmd,
                lambda event: self._handle_event(feed_name, feed_config, event),
                reconnect=reconnect,
                buffer_size=buffer_size,
                log_callback=self._log
            )
            process.start()
            self.streams.setdefault(feed_name, []).append(process)

            self._log(f"  Started {stream_id}")

        elif relay_resolution.strategy == 'grouped':
            # Multiple processes (one per relay group)
            for relay, authors in relay_resolution.relays.items():
                stream_id = f"{feed_name}:{relay}"
                cmd = self._build_command(feed_config, [relay], authors)

                process = StreamProcess(
                    stream_id,
                    cmd,
                    lambda event: self._handle_event(feed_name, feed_config, event),
                    reconnect=reconnect,
                    buffer_size=buffer_size,
                    log_callback=self._log
                )
                process.start()
                self.streams.setdefault(feed_name, []).append(process)

                self._log(f"  Started {stream_id} ({len(authors)} authors)")

    def _build_command(
        self,
        feed_config: Dict[str, Any],
        relays: List[str],
        authors: Optional[List[str]] = None
    ) -> List[str]:
        """
        Build nak req --stream command.

        Args:
            feed_config: Feed configuration
            relays: List of relay URLs
            authors: Optional list of author pubkeys

        Returns:
            Command list for subprocess
        """
        cmd = ['nak', 'req']

        # Add kind filters (support both single kind and multiple kinds)
        root = feed_config.get('root', {})

        # Check for explicit kinds list first
        kinds = feed_config.get('stream_kinds') or root.get('kinds')

        if kinds:
            # Multiple kinds
            for k in kinds:
                cmd.extend(['-k', str(k)])
        else:
            # Single kind (legacy)
            kind = root.get('kind')
            if kind:
                cmd.extend(['-k', str(kind)])

        # Add author filters
        if authors:
            for author in authors:
                cmd.extend(['-a', author])

        # Add filter constraints
        root_filter = feed_config.get('root', {}).get('filter', {})
        if root_filter.get('no_e_tags'):
            cmd.append('--no-e-tags')

        # Add streaming flag
        cmd.append('--stream')

        # Add relays
        cmd.extend(relays)

        return cmd

    def _handle_event(self, feed_name: str, feed_config: Dict, event: Dict):
        """
        Handle received event (store to database).

        Args:
            feed_name: Feed name
            feed_config: Feed configuration
            event: Event dictionary
        """
        try:
            # Get feed database
            db_file = feed_config.get('db_file', f"{feed_name}.db")
            feed_db = self.db_manager.get_connection(db_file)

            # Store event
            store_event(feed_db, event)

        except Exception as e:
            self._log(f"[{feed_name}] Error storing event: {e}")

    def stop_feed(self, feed_name: str):
        """
        Stop all streams for a feed.

        Args:
            feed_name: Feed to stop
        """
        if feed_name not in self.streams:
            return

        self._log(f"Stopping streams for '{feed_name}'...")

        for process in self.streams[feed_name]:
            process.stop()

        del self.streams[feed_name]
        self._log(f"  Stopped '{feed_name}'")

    def stop_all(self):
        """Stop all streams."""
        self._log("Stopping all streams...")

        for feed_name in list(self.streams.keys()):
            self.stop_feed(feed_name)

        self._log("All streams stopped")

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all streams.

        Returns:
            Dictionary mapping feed names to status info
        """
        status = {}

        for feed_name, processes in self.streams.items():
            total_events = sum(p.stats.events_received for p in processes)
            total_errors = sum(p.stats.errors for p in processes)
            alive_count = sum(1 for p in processes if p.is_alive())

            status[feed_name] = {
                'running': alive_count > 0,
                'processes': len(processes),
                'alive': alive_count,
                'events_received': total_events,
                'errors': total_errors
            }

        return status

    def _default_log(self, message: str):
        """Default logging to stderr."""
        print(f"[stream-manager] {message}", file=sys.stderr, flush=True)
