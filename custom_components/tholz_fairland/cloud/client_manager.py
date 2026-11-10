"""Polling manager for one Fairland cloud client."""

import asyncio
import copy
import logging
import time
from contextlib import suppress

_LOGGER = logging.getLogger(__name__)

FRESHNESS_POLL_MULTIPLIER = 3
FRESHNESS_MARGIN_SECONDS = 5
# Verified heat0 type of the Fairland / TurboSilence heat pump. Other families
# are intentionally not admitted by the cloud read gate.
FAIRLAND_HEAT_TYPE = 7
ERR_NO_VALID_READ = "Fairland cloud returned no valid state"
ERR_MANAGER_CLOSED = "Fairland cloud manager is closed; commands are refused"


class FairlandReadUnavailableError(RuntimeError):
    """No valid cloud read is available to answer a setup or status request."""


class FairlandManagerClosedError(RuntimeError):
    """The manager was stopped; no further cloud commands may be issued."""


def _is_valid_snapshot(document):
    """
    Return whether a read has the verified Fairland heat0/type-7 structure.

    Only structure is checked. ``connected`` is telemetry, so a cloud report of
    ``False`` remains a valid read. ``type`` must be a real integer: ``True``
    compares equal to ``1`` in Python and is never accepted here.
    """
    if not isinstance(document, dict):
        return False
    heatings = document.get("heatings")
    if not isinstance(heatings, dict):
        return False
    heat0 = heatings.get("heat0")
    if not isinstance(heat0, dict):
        return False
    heat_type = heat0.get("type")
    return (
        isinstance(heat_type, int)
        and not isinstance(heat_type, bool)
        and heat_type == FAIRLAND_HEAT_TYPE
    )


class FairlandClientManager:
    """
    Cache cloud reads and command results; serialize all client access.

    Reads (``_read_data``) and command results (``_data``) are separate caches.
    Only a successful read advances the freshness clock; a command result never
    proves that the device's telemetry is current. ``stop()`` is terminal: it
    invalidates every cached read and refuses further commands.
    """

    def __init__(self, client, polling_interval: int):
        self._client = client
        self._polling_interval = polling_interval
        self._read_data = None
        self._read_at = None
        self._data = None
        self._confirmed_controls = {}
        self._closed = False
        self._lock = asyncio.Lock()
        self._task = None
        self._wake_event = asyncio.Event()
        self._loop = None

    @property
    def freshness_window(self) -> float:
        """Maximum monotonic age, in seconds, of a read that still counts as fresh."""
        return (
            FRESHNESS_POLL_MULTIPLIER * self._polling_interval
            + FRESHNESS_MARGIN_SECONDS
        )

    @property
    def is_fresh(self) -> bool:
        """Return whether the latest valid read is within the freshness window."""
        if self._closed or self._read_data is None or self._read_at is None:
            return False
        return time.monotonic() - self._read_at <= self.freshness_window

    def _invalidate_read(self) -> None:
        self._read_data = None
        self._read_at = None
        self._confirmed_controls.clear()

    def start(self, hass):
        # A stopped manager is terminal; a reload builds a new manager instead.
        if self._task is None and not self._closed:
            self._loop = asyncio.get_running_loop()
            set_callback = getattr(self._client, "set_state_change_callback", None)
            if set_callback is not None:
                set_callback(self._request_refresh)
            self._task = hass.async_create_background_task(
                self._updater(), "Tholz Fairland polling updater"
            )

    def _request_refresh(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def stop(self):
        # Mark closed and invalidate first so no entity can claim a reading once
        # unload begins, even while a worker-thread read is still running.
        self._closed = True
        self._invalidate_read()
        self._data = None
        set_callback = getattr(self._client, "set_state_change_callback", None)
        if set_callback is not None:
            set_callback(None)
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._loop = None
        self._invalidate_read()
        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    async def _fetch_data(self):
        """Read the device; a failed or structurally invalid read drops the cache."""
        if self._closed:
            self._invalidate_read()
            return None
        try:
            data = await asyncio.to_thread(self._client.get_status)
        except Exception:
            self._invalidate_read()
            raise
        if self._closed:
            self._invalidate_read()
            return None
        if not _is_valid_snapshot(data):
            self._invalidate_read()
            _LOGGER.warning("Tholz polling returned an invalid Fairland state")
            return None
        self._read_data = copy.deepcopy(data)
        self._read_at = time.monotonic()
        self._confirmed_controls.clear()
        return data

    async def _updater(self):
        while True:
            try:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=self._polling_interval
                    )
                self._wake_event.clear()
                async with self._lock:
                    await self._fetch_data()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("Tholz polling update failed: %s", exc)

    async def get_status(self):
        """Return a copy of a valid read, reading once if absent or expired."""
        async with self._lock:
            if not self.is_fresh:
                await self._fetch_data()
            if not self.is_fresh:
                raise FairlandReadUnavailableError(ERR_NO_VALID_READ)
            return await self.get_sensor_status()

    async def get_sensor_status(self):
        """Return a copy of the latest fresh read, or None. Never performs I/O."""
        if not self.is_fresh or self._read_data is None:
            return None
        snapshot = copy.deepcopy(self._read_data)
        # Display only confirmed writable fields over the older read. Do not
        # replace temperatures or renew their validity with a command result.
        snapshot["heatings"]["heat0"].update(self._confirmed_controls)
        return snapshot

    async def set_status(self, payload):
        """Keep confirmed controls without making sensor telemetry younger."""
        if self._closed:
            raise FairlandManagerClosedError(ERR_MANAGER_CLOSED)
        async with self._lock:
            if self._closed:
                raise FairlandManagerClosedError(ERR_MANAGER_CLOSED)
            try:
                result = await asyncio.to_thread(self._client.set_status, payload)
            except Exception:
                self._invalidate_read()
                raise
            if self._closed:
                raise FairlandManagerClosedError(ERR_MANAGER_CLOSED)
            if result and isinstance(result, dict):
                self._data = copy.deepcopy(result)
                requested = payload.get("heatings", {}).get("heat0", {})
                if _is_valid_snapshot(result) and isinstance(requested, dict):
                    confirmed = result["heatings"]["heat0"]
                    for field in ("sp", "opMode", "fanMode"):
                        if field in requested and field in confirmed:
                            self._confirmed_controls[field] = confirmed[field]
            return result
