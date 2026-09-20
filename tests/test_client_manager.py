import asyncio

import pytest

from custom_components.tholz_fairland.cloud.client_manager import FairlandClientManager


class FakeClient:
    def __init__(self):
        self.closed = False
        self.callback = None
        self.reads = 0

    def get_status(self):
        self.reads += 1
        return {"id": 1496}

    def close(self):
        self.closed = True

    def set_state_change_callback(self, callback):
        self.callback = callback


class FakeHass:
    def __init__(self):
        self.background_calls = []

    def async_create_background_task(self, coroutine, name):
        self.background_calls.append(name)
        return asyncio.create_task(coroutine)


@pytest.mark.asyncio
async def test_polling_uses_non_blocking_background_task():
    client = FakeClient()
    manager = FairlandClientManager(client, polling_interval=300)
    hass = FakeHass()

    manager.start(hass)
    await asyncio.sleep(0)

    assert hass.background_calls == ["Tholz Fairland polling updater"]
    assert manager._task is not None

    await manager.stop()

    assert manager._task is None
    assert client.closed is True
    assert client.callback is None


@pytest.mark.asyncio
async def test_external_state_change_wakes_polling_manager():
    client = FakeClient()
    manager = FairlandClientManager(client, polling_interval=300)
    hass = FakeHass()

    manager.start(hass)
    await asyncio.sleep(0)
    assert client.reads == 0

    assert client.callback is not None
    client.callback()
    await asyncio.sleep(0.01)

    assert client.reads == 1
    assert manager._data == {"id": 1496}

    client.callback()
    await asyncio.sleep(0.01)

    assert client.reads == 2

    await manager.stop()
