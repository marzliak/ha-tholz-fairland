"""Polling manager for one Fairland cloud client."""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class FairlandClientManager:
    """Cache cloud state and serialize reads and writes for all entities."""

    def __init__(self, client, polling_interval: int):
        self._client = client
        self._polling_interval = polling_interval
        self._data = None
        self._last_update = None
        self._lock = asyncio.Lock()
        self._task = None
        self._wake_event = asyncio.Event()
        self._loop = None

    def start(self, hass):
        if self._task is None:
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
        set_callback = getattr(self._client, "set_state_change_callback", None)
        if set_callback is not None:
            set_callback(None)
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._loop = None
        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    async def _fetch_data(self):
        data = await asyncio.to_thread(self._client.get_status)
        if data is not None:
            self._data = data
            self._last_update = datetime.now()
        return self._data

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
        async with self._lock:
            if self._data is None:
                await self._fetch_data()
            return self._data

    async def set_status(self, payload):
        async with self._lock:
            result = await asyncio.to_thread(self._client.set_status, payload)
            if result and isinstance(result, dict):
                self._data = result
                self._last_update = datetime.now()
            return result
