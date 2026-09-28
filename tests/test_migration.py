"""
Config-entry migration tests: schema 1.1 -> 1.2 (``async_migrate_entry``).

The migration is a MINOR bump so a rollback build (schema 1.1, same major
version) can still load a migrated entry. Entries here are real Home Assistant
``ConfigEntry`` objects, which refuse direct mutation of data, options and
versions; the fake ``async_update_entry`` applies changes exactly the way Home
Assistant does. Every credential is synthetic.
"""

import copy
import inspect
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry

from custom_components.tholz_fairland import (
    _normalize,
    _resolve_credentials,
    _resolve_mode,
    async_migrate_entry,
)
from custom_components.tholz_fairland.cloud.auth_manager import RecoveryMode
from custom_components.tholz_fairland.config_flow import TholzFairlandConfigFlow
from custom_components.tholz_fairland.utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_PASSWORD_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_COGNITO_USERNAME_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_NAME_KEY,
    CONF_POLLING_INTERVAL_KEY,
    CONF_RECOVERY_MODE_KEY,
    CONF_THING_ID_KEY,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    RECOVERY_MODE_AUTOMATIC,
    RECOVERY_MODE_INTERACTIVE,
)

ENTRY_ID = "synthetic-entry-id"
THING_ID = "synthetic-thing-id"
TITLE = "Fairland"
USERNAME = "owner@example.invalid"
PASSWORD = "synthetic-password"

# The exact data shape the schema 1.1 config flow created.
LEGACY_DATA = {
    CONF_NAME_KEY: TITLE,
    CONF_THING_ID_KEY: THING_ID,
    CONF_COGNITO_USER_SUB_KEY: "synthetic-sub",
    CONF_COGNITO_CLIENT_SECRET_KEY: "synthetic-client-secret",
    CONF_COGNITO_REFRESH_TOKEN_KEY: "rt-live",
    CONF_POLLING_INTERVAL_KEY: 30,
}
# The exact options shape the schema 1.1 options flow wrote.
LEGACY_OPTIONS = {
    CONF_COGNITO_USER_SUB_KEY: "synthetic-sub-edited",
    CONF_COGNITO_CLIENT_SECRET_KEY: "synthetic-client-secret-edited",
    CONF_COGNITO_REFRESH_TOKEN_KEY: "rt-from-options",
    CONF_POLLING_INTERVAL_KEY: 60,
}
ACCEPTED_UPDATE_KEYS = {"data", "options", "minor_version"}


class FakeConfigEntries:
    """Apply updates the way ``ConfigEntries.async_update_entry`` does."""

    def __init__(self):
        self.updates = []

    def async_update_entry(self, entry, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            stored = (
                MappingProxyType(dict(value)) if key in {"data", "options"} else value
            )
            # Home Assistant bypasses ConfigEntry.__setattr__ the same way.
            object.__setattr__(entry, key, stored)
        return True


# The ConfigEntry constructor grows required keywords across Home Assistant
# releases (e.g. ``subentries_data``); pass only the ones this release accepts.
_ENTRY_PARAMETERS = frozenset(inspect.signature(ConfigEntry).parameters)


def _entry(data, options=None, *, version=1, minor_version=1):
    candidates = {
        "data": data,
        "discovery_keys": MappingProxyType({}),
        "domain": DOMAIN,
        "entry_id": ENTRY_ID,
        "minor_version": minor_version,
        "options": options or {},
        "source": "user",
        "subentries_data": (),
        "title": TITLE,
        "unique_id": THING_ID,
        "version": version,
    }
    return ConfigEntry(
        **{key: value for key, value in candidates.items() if key in _ENTRY_PARAMETERS}
    )


async def _migrate(data, options=None, **versions):
    """Run the migration on a fresh entry; return (result, entry, updates)."""
    entry = _entry(copy.deepcopy(data), copy.deepcopy(options), **versions)
    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    result = await async_migrate_entry(hass, entry)
    return result, entry, hass.config_entries.updates


def _baseline_credentials(settings):
    """Credential resolution of the schema 1.1 build (the rollback target)."""
    live = settings.get(CONF_COGNITO_REFRESH_TOKEN_KEY) or ""
    seed = settings.get(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY) or live or None
    return live, seed


# -- versioning ---------------------------------------------------------------------
def test_flow_keeps_major_version_one_and_declares_the_minor_bump():
    """
    Keep the major version so a rollback build still loads migrated entries.

    The minor bump is also what makes Home Assistant call
    ``async_migrate_entry`` for a 1.1 entry at all.
    """
    assert TholzFairlandConfigFlow.VERSION == CONFIG_ENTRY_VERSION == 1
    assert TholzFairlandConfigFlow.MINOR_VERSION == CONFIG_ENTRY_MINOR_VERSION == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("minor_version", [CONFIG_ENTRY_MINOR_VERSION, 3])
async def test_current_or_newer_minor_version_is_left_untouched(minor_version):
    result, entry, updates = await _migrate(LEGACY_DATA, minor_version=minor_version)

    assert result is True
    assert updates == []
    assert dict(entry.data) == LEGACY_DATA


@pytest.mark.asyncio
async def test_newer_major_version_is_refused_without_a_write():
    result, entry, updates = await _migrate(LEGACY_DATA, version=2)

    assert result is False
    assert updates == []
    assert dict(entry.data) == LEGACY_DATA


@pytest.mark.asyncio
async def test_update_bumps_only_the_minor_version_through_home_assistant():
    result, entry, updates = await _migrate(LEGACY_DATA)

    assert result is True
    (update,) = updates
    assert set(update) == ACCEPTED_UPDATE_KEYS
    assert update["minor_version"] == CONFIG_ENTRY_MINOR_VERSION
    assert (entry.version, entry.minor_version) == (1, 2)
    accepted = set(inspect.signature(ConfigEntries.async_update_entry).parameters)
    assert set(update) <= accepted


# -- legacy shapes ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_entry_with_only_a_current_token_gains_a_seed_and_mode():
    _, entry, _ = await _migrate(LEGACY_DATA)

    # Exact equality: every legacy key survives, nothing (password, username)
    # is invented, and the seed is copied from the current token.
    assert dict(entry.data) == {
        **LEGACY_DATA,
        CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-live",
        CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_INTERACTIVE,
    }
    assert dict(entry.options) == {}


@pytest.mark.asyncio
async def test_distinct_current_token_and_seed_are_both_preserved():
    data = {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed"}

    _, entry, _ = await _migrate(data)

    assert entry.data[CONF_COGNITO_REFRESH_TOKEN_KEY] == "rt-live"
    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == "rt-seed"


@pytest.mark.asyncio
@pytest.mark.parametrize("unusable_seed", ["", None, 42])
async def test_missing_or_unusable_seed_is_filled_from_the_current_token(
    unusable_seed,
):
    data = {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: unusable_seed}

    _, entry, _ = await _migrate(data)

    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == "rt-live"


@pytest.mark.asyncio
async def test_credentials_shadowed_in_options_move_into_data():
    data = {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed"}

    _, entry, _ = await _migrate(data, LEGACY_OPTIONS)

    # The option values were the effective ones, so they become canonical.
    for key in (
        CONF_COGNITO_REFRESH_TOKEN_KEY,
        CONF_COGNITO_USER_SUB_KEY,
        CONF_COGNITO_CLIENT_SECRET_KEY,
    ):
        assert entry.data[key] == LEGACY_OPTIONS[key]
    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == "rt-seed"
    # Only the polling interval may live in options.
    assert dict(entry.options) == {CONF_POLLING_INTERVAL_KEY: 60}


@pytest.mark.asyncio
async def test_seed_is_filled_from_the_effective_token_not_the_shadowed_one():
    options = {CONF_COGNITO_REFRESH_TOKEN_KEY: "rt-from-options"}

    _, entry, _ = await _migrate(LEGACY_DATA, options)

    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == "rt-from-options"


@pytest.mark.asyncio
async def test_seed_shadowed_in_options_moves_into_data():
    options = {CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed-from-options"}

    _, entry, _ = await _migrate(LEGACY_DATA, options)

    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == "rt-seed-from-options"
    assert dict(entry.options) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", ["", None])
async def test_empty_option_values_are_dropped_without_erasing_data(empty):
    options = {
        CONF_COGNITO_REFRESH_TOKEN_KEY: empty,
        CONF_COGNITO_USER_SUB_KEY: empty,
        CONF_COGNITO_CLIENT_SECRET_KEY: empty,
    }

    _, entry, _ = await _migrate(LEGACY_DATA, options)

    for key in options:
        assert entry.data[key] == LEGACY_DATA[key]
    assert dict(entry.options) == {}


@pytest.mark.asyncio
async def test_entry_without_any_token_migrates_without_inventing_one():
    data = {
        key: value
        for key, value in LEGACY_DATA.items()
        if key != CONF_COGNITO_REFRESH_TOKEN_KEY
    }

    result, entry, _ = await _migrate(data)

    assert result is True
    assert CONF_COGNITO_REFRESH_TOKEN_KEY not in entry.data
    assert CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY not in entry.data
    assert entry.data[CONF_RECOVERY_MODE_KEY] == RECOVERY_MODE_INTERACTIVE


@pytest.mark.asyncio
async def test_unknown_keys_survive_the_migration():
    data = {**LEGACY_DATA, "future_data_key": {"nested": True}}
    options = {"future_option_key": [1, 2]}

    _, entry, _ = await _migrate(data, options)

    assert entry.data["future_data_key"] == {"nested": True}
    assert dict(entry.options) == {"future_option_key": [1, 2]}


# -- identity -------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_identity_and_ownership_are_preserved():
    options = {**LEGACY_OPTIONS, CONF_THING_ID_KEY: "some-other-thing"}

    _, entry, updates = await _migrate(LEGACY_DATA, options)

    assert entry.entry_id == ENTRY_ID
    assert entry.unique_id == THING_ID
    assert entry.title == TITLE
    assert entry.domain == DOMAIN
    assert entry.version == CONFIG_ENTRY_VERSION
    # thing_id is identity: never taken from options, never rewritten.
    assert entry.data[CONF_THING_ID_KEY] == THING_ID
    assert CONF_THING_ID_KEY not in entry.options
    assert entry.data[CONF_NAME_KEY] == TITLE
    assert all(set(update) <= ACCEPTED_UPDATE_KEYS for update in updates)


# -- recovery mode and password consent -------------------------------------------
@pytest.mark.asyncio
async def test_automatic_mode_with_a_password_is_kept():
    data = {
        **LEGACY_DATA,
        CONF_COGNITO_USERNAME_KEY: USERNAME,
        CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_AUTOMATIC,
        CONF_COGNITO_PASSWORD_KEY: PASSWORD,
    }

    _, entry, _ = await _migrate(data)

    assert entry.data[CONF_RECOVERY_MODE_KEY] == RECOVERY_MODE_AUTOMATIC
    assert entry.data[CONF_COGNITO_PASSWORD_KEY] == PASSWORD
    assert entry.data[CONF_COGNITO_USERNAME_KEY] == USERNAME


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "password"),
    [
        pytest.param(RECOVERY_MODE_AUTOMATIC, None, id="automatic-without-password"),
        pytest.param(RECOVERY_MODE_AUTOMATIC, "", id="automatic-empty-password"),
        pytest.param(RECOVERY_MODE_INTERACTIVE, PASSWORD, id="interactive-stray"),
        pytest.param("bogus", PASSWORD, id="unknown-mode"),
        pytest.param(None, None, id="null-mode"),
        pytest.param(5, None, id="non-string-mode"),
    ],
)
async def test_inconsistent_recovery_settings_become_interactive_without_password(
    mode, password
):
    data = {**LEGACY_DATA, CONF_RECOVERY_MODE_KEY: mode}
    if password is not None:
        data[CONF_COGNITO_PASSWORD_KEY] = password

    _, entry, _ = await _migrate(data)

    assert entry.data[CONF_RECOVERY_MODE_KEY] == RECOVERY_MODE_INTERACTIVE
    assert CONF_COGNITO_PASSWORD_KEY not in entry.data


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["", None, 7])
async def test_unusable_username_is_dropped(username):
    data = {**LEGACY_DATA, CONF_COGNITO_USERNAME_KEY: username}

    _, entry, _ = await _migrate(data)

    assert CONF_COGNITO_USERNAME_KEY not in entry.data


@pytest.mark.asyncio
async def test_recovery_settings_shadowed_in_options_are_honoured_then_moved():
    options = {
        CONF_COGNITO_USERNAME_KEY: USERNAME,
        CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_AUTOMATIC,
        CONF_COGNITO_PASSWORD_KEY: PASSWORD,
    }

    _, entry, _ = await _migrate(LEGACY_DATA, options)

    assert entry.data[CONF_RECOVERY_MODE_KEY] == RECOVERY_MODE_AUTOMATIC
    assert entry.data[CONF_COGNITO_PASSWORD_KEY] == PASSWORD
    assert entry.data[CONF_COGNITO_USERNAME_KEY] == USERNAME
    assert dict(entry.options) == {}


# -- idempotency ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rerunning_the_migration_is_idempotent():
    data = {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed"}
    _, first, _ = await _migrate(data, LEGACY_OPTIONS)

    # Re-migrating the migrated payload (e.g. relabelled 1.1) changes nothing...
    _, second, _ = await _migrate(dict(first.data), dict(first.options))
    assert dict(second.data) == dict(first.data)
    assert dict(second.options) == dict(first.options)

    # ...and a 1.2 entry is never written again.
    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    assert await async_migrate_entry(hass, first) is True
    assert hass.config_entries.updates == []


@pytest.mark.parametrize(
    ("data", "options"),
    [
        pytest.param({}, {}, id="empty"),
        pytest.param(LEGACY_DATA, LEGACY_OPTIONS, id="legacy-shadowed"),
        pytest.param(
            {
                CONF_COGNITO_REFRESH_TOKEN_KEY: 12,
                CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: ["x"],
                CONF_RECOVERY_MODE_KEY: 3,
                CONF_COGNITO_PASSWORD_KEY: 4,
                CONF_COGNITO_USERNAME_KEY: {},
            },
            {},
            id="wrong-types",
        ),
        pytest.param(
            {CONF_COGNITO_REFRESH_TOKEN_KEY: None},
            {
                CONF_COGNITO_REFRESH_TOKEN_KEY: 0,
                CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: None,
                CONF_THING_ID_KEY: None,
            },
            id="null-values",
        ),
        pytest.param(
            LEGACY_DATA,
            {CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_AUTOMATIC},
            id="automatic-without-password",
        ),
    ],
)
def test_normalization_is_total_pure_and_idempotent(data, options):
    data_before, options_before = copy.deepcopy(data), copy.deepcopy(options)

    once = _normalize(data, options)
    twice = _normalize(*once)

    assert twice == once
    assert data == data_before
    assert options == options_before


# -- rollback compatibility ------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        pytest.param(LEGACY_DATA, None, ("rt-live", "rt-live"), id="current-only"),
        pytest.param(
            {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed"},
            None,
            ("rt-live", "rt-seed"),
            id="distinct-seed",
        ),
        pytest.param(
            {**LEGACY_DATA, CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: "rt-seed"},
            LEGACY_OPTIONS,
            ("rt-from-options", "rt-seed"),
            id="options-shadowed",
        ),
    ],
)
async def test_migrated_entry_stays_loadable_by_the_rollback_build(
    data, options, expected
):
    _, entry, _ = await _migrate(data, options)
    settings = {**entry.data, **entry.options}

    # Same major version: Home Assistant hands a 1.2 entry to a 1.1 build.
    assert entry.version == CONFIG_ENTRY_VERSION
    # Keys the 1.1 build reads without a default are all still present.
    assert settings[CONF_COGNITO_USER_SUB_KEY]
    assert settings[CONF_COGNITO_CLIENT_SECRET_KEY]
    assert entry.data[CONF_THING_ID_KEY] == THING_ID
    # Both builds resolve the same live token and seed from the migrated entry.
    assert _baseline_credentials(settings) == expected
    assert _resolve_credentials(settings) == expected
    assert _resolve_mode(settings) is RecoveryMode.INTERACTIVE
