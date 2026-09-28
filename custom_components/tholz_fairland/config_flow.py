"""Configuration flow for the Tholz Fairland cloud integration."""

from __future__ import annotations

from contextlib import suppress
import threading
from typing import Any

from aiohttp import ClientError, ClientTimeout
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .cloud.auth_manager import FairlandAuthManager, RecoveryMode
from .cloud.client import (
    API_BASE_URL,
    REGION,
    USER_POOL_CLIENT_ID,
    USER_POOL_ID,
    FairlandCloudAuthError,
    FairlandCloudClient,
)
from .cloud.cognito_auth import (
    CHALLENGE_NEW_PASSWORD_REQUIRED,
    AuthSuccess,
    ChallengeRequired,
    ChallengeState,
    CognitoAuthenticator,
    CognitoChallengeRequired,
    CognitoChallengeResponseRejected,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
)
from .utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_PASSWORD_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_COGNITO_USERNAME_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_NAME_KEY,
    CONF_POLLING_INTERVAL_DEFAULT_VALUE,
    CONF_POLLING_INTERVAL_KEY,
    CONF_RECOVERY_MODE_KEY,
    CONF_THING_ID_KEY,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    RECOVERY_MODE_AUTOMATIC,
)

ERR_CANNOT_CONNECT = "cannot_connect"
ERR_INVALID_TOKEN = "invalid_token"  # noqa: S105
ERR_INVALID_AUTH = "invalid_auth"
ERR_UNSUPPORTED_CHALLENGE = "unsupported_challenge"
ERR_CHALLENGE_FAILED = "challenge_failed"
ERR_CONFIGURATION = "configuration_error"
ERR_WRONG_ACCOUNT = "wrong_account"
ERR_NO_DEVICES = "no_devices"

CONF_CHALLENGE_RESPONSE_KEY = "challenge_response"

_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_SUPPORTED_PRODUCT_IDS = {1496}
_SUPPORTED_PRODUCT_MARKERS = ("fairland", "turbosilence")
_HTTP_ERROR_MIN = 400


def _suggest(value: str) -> dict[str, str]:
    """Prefill a non-secret field without echoing a secret as a form default."""
    return {"suggested_value": value}


def _cloud_schema(defaults: dict | None = None) -> vol.Schema:
    """Collect only normal account credentials and user-facing preferences."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME_KEY, default=defaults.get(CONF_NAME_KEY, "Fairland")
            ): str,
            vol.Required(
                CONF_COGNITO_USERNAME_KEY,
                description=_suggest(defaults.get(CONF_COGNITO_USERNAME_KEY, "")),
            ): str,
            vol.Required(CONF_COGNITO_PASSWORD_KEY): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_POLLING_INTERVAL_KEY,
                default=defaults.get(
                    CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
                ),
            ): vol.All(int, vol.Range(min=10, max=300)),
        }
    )


def _device_schema(devices: dict[str, str]) -> vol.Schema:
    """Let the user select one supported heat pump from the account."""
    return vol.Schema({vol.Required(CONF_THING_ID_KEY): vol.In(devices)})


def _options_schema(defaults: dict) -> vol.Schema:
    """Options own only the polling interval; credentials live in entry data."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_POLLING_INTERVAL_KEY,
                default=defaults.get(
                    CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
                ),
            ): vol.All(int, vol.Range(min=10, max=300)),
        }
    )


def _reauth_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COGNITO_USERNAME_KEY,
                description=_suggest(defaults.get(CONF_COGNITO_USERNAME_KEY, "")),
            ): str,
            vol.Required(CONF_COGNITO_PASSWORD_KEY): _PASSWORD_SELECTOR,
        }
    )


def _reconfigure_schema(defaults: dict) -> vol.Schema:
    return _reauth_schema(defaults)


def _challenge_schema(challenge_name: str | None = None) -> vol.Schema:
    # A new password is masked; a one-time code is shown as the user types it.
    field: Any = (
        _PASSWORD_SELECTOR if challenge_name == CHALLENGE_NEW_PASSWORD_REQUIRED else str
    )
    return vol.Schema({vol.Required(CONF_CHALLENGE_RESPONSE_KEY): field})


async def _validate_cloud(hass, values: dict) -> str:
    """Read state and return the newest refresh token produced by validation."""
    initial_token = values[CONF_COGNITO_REFRESH_TOKEN_KEY]
    latest_token = initial_token
    accepted_generation = -1
    callback_closed = False
    callback_lock = threading.Lock()
    manager = FairlandAuthManager(
        CognitoAuthenticator(REGION, USER_POOL_ID, USER_POOL_CLIENT_ID),
        user_sub=values[CONF_COGNITO_USER_SUB_KEY],
        client_secret=None,
        current_token=initial_token,
        recovery_token=None,
        mode=RecoveryMode.INTERACTIVE,
    )

    def _capture_rotation(update) -> None:
        nonlocal accepted_generation, latest_token
        with callback_lock:
            if callback_closed:
                return
            if update.expected_current != initial_token:
                return
            if update.generation <= accepted_generation:
                return
            if not isinstance(update.current_token, str) or not update.current_token:
                return
            accepted_generation = update.generation
            latest_token = update.current_token

    manager.set_persist_callback(_capture_rotation)
    client = FairlandCloudClient(
        thing_id=values[CONF_THING_ID_KEY],
        auth_manager=manager,
    )
    try:
        await hass.async_add_executor_job(client.get_status)
    except FairlandCloudAuthError as exc:
        raise ValueError(ERR_INVALID_TOKEN) from exc
    except Exception as exc:
        raise ValueError(ERR_CANNOT_CONNECT) from exc
    finally:
        # Close the local capture gate before detaching the callback. A worker
        # may have retained the callable reference; any late/out-of-order write
        # must be unable to change the token snapshot returned by this flow.
        with callback_lock:
            callback_closed = True
            final_token = latest_token
        manager.set_persist_callback(None)
        await hass.async_add_executor_job(client.close)
    return final_token


def _supported_devices(payload: object) -> dict[str, str]:
    """Return a sanitized thing-id to display-label map for supported products."""
    if not isinstance(payload, dict) or not isinstance(payload.get("devices"), list):
        raise ValueError(ERR_CANNOT_CONNECT)  # noqa: TRY004
    devices: dict[str, str] = {}
    for raw in payload["devices"]:
        if not isinstance(raw, dict):
            continue
        thing_id = str(raw.get("thingId", "")).strip()
        product_name = str(raw.get("productName", "")).strip()
        product_id = raw.get("productId")
        supported_id = False
        if product_id is not None:
            with suppress(TypeError, ValueError):
                supported_id = int(product_id) in _SUPPORTED_PRODUCT_IDS
        supported_name = any(
            marker in product_name.casefold() for marker in _SUPPORTED_PRODUCT_MARKERS
        )
        if not thing_id or not (supported_id or supported_name):
            continue
        device_name = str(raw.get("name", "")).strip()
        devices[thing_id] = device_name or product_name or "Fairland heat pump"
    return devices


async def _discover_devices(hass, id_token: str) -> dict[str, str]:
    """List compatible devices with the already authenticated account."""
    try:
        session = async_get_clientsession(hass)
        async with session.get(
            f"{API_BASE_URL}/",
            headers={"Authorization": id_token},
            timeout=ClientTimeout(total=15),
        ) as response:
            if response.status in {401, 403}:
                _raise_flow_error(ERR_INVALID_AUTH)
            if response.status >= _HTTP_ERROR_MIN:
                _raise_flow_error(ERR_CANNOT_CONNECT)
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError) as exc:
                raise ValueError(ERR_CANNOT_CONNECT) from exc
    except ValueError:
        raise
    except (ClientError, TimeoutError, TypeError) as exc:
        raise ValueError(ERR_CANNOT_CONNECT) from exc
    return _supported_devices(payload)


def _raise_flow_error(reason: str) -> None:
    """Raise a translated flow error outside the network try block."""
    raise ValueError(reason)


def _sanitized_error(exc: Exception) -> str:
    """Map a typed Cognito failure to a translated, secret-free error key."""
    if isinstance(exc, CognitoChallengeResponseRejected):
        return ERR_CHALLENGE_FAILED
    if isinstance(exc, CognitoCredentialRejected):
        return ERR_INVALID_AUTH
    if isinstance(exc, CognitoConfigurationError):
        return ERR_CONFIGURATION
    if isinstance(exc, CognitoChallengeRequired):
        return ERR_UNSUPPORTED_CHALLENGE
    if isinstance(exc, (CognitoTransientError, CognitoProtocolError)):
        return ERR_CANNOT_CONNECT
    return ERR_CANNOT_CONNECT


def _existing_subject_error(entry, outcome: AuthSuccess) -> str | None:
    """Fail closed when reauth cannot prove it belongs to the existing account."""
    expected_subject = entry.data.get(CONF_COGNITO_USER_SUB_KEY)
    if not (isinstance(expected_subject, str) and expected_subject.strip()):
        return ERR_CONFIGURATION
    if outcome.subject != expected_subject:
        return ERR_WRONG_ACCOUNT
    return None


class TholzFairlandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a cloud-connected Fairland heat pump."""

    VERSION = CONFIG_ENTRY_VERSION
    MINOR_VERSION = CONFIG_ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        # Challenge and plaintext-password state exists only for this flow and is
        # scrubbed before returning. The password is persisted only after the
        # account and a read-only device request have both been validated.
        self._authenticator: CognitoAuthenticator | None = None
        self._challenge_state: ChallengeState | None = None
        self._reauth_username: str | None = None
        self._reauth_password: str | None = None
        self._reauth_client_secret: str | None = None
        self._flow_kind: str | None = None
        self._target_entry = None
        self._pending_user_input: dict[str, Any] = {}
        self._pending_success: AuthSuccess | None = None
        self._verified_refresh_token: str | None = None
        self._devices: dict[str, str] = {}
        self._removed = False

    @callback
    def async_remove(self) -> None:
        """Scrub credentials and challenge state when Home Assistant removes the flow."""
        self._removed = True
        self._challenge_state = None
        self._reauth_password = None
        self._pending_success = None
        self._verified_refresh_token = None
        self._pending_user_input = {}
        self._devices = {}

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_kind = "user"
            self._target_entry = None
            self._pending_user_input = dict(user_input)
            self._reauth_client_secret = None
            self._reauth_username = user_input[CONF_COGNITO_USERNAME_KEY]
            self._reauth_password = user_input[CONF_COGNITO_PASSWORD_KEY]
            outcome, error = await self._run_srp_login()
            if self._removed:
                return self.async_abort(reason="user_cancelled")
            if error is not None:
                errors["base"] = error
            elif isinstance(outcome, ChallengeRequired):
                self._challenge_state = outcome.state
                return await self.async_step_challenge()
            elif isinstance(outcome, AuthSuccess):
                result, error = await self._finish_user_auth(outcome)
                if result is not None:
                    return result
                errors["base"] = error or ERR_CANNOT_CONNECT
        return self.async_show_form(
            step_id="user", data_schema=_cloud_schema(user_input), errors=errors
        )

    async def async_step_select_device(self, user_input=None):
        """Select among multiple supported devices associated with the account."""
        if not self._devices or self._pending_success is None:
            return self.async_abort(reason="configuration_error")
        errors: dict[str, str] = {}
        if user_input is not None:
            thing_id = user_input[CONF_THING_ID_KEY]
            if thing_id not in self._devices:
                errors["base"] = ERR_NO_DEVICES
            else:
                result, error = await self._create_user_entry(thing_id)
                if result is not None:
                    return result
                errors["base"] = error or ERR_CANNOT_CONNECT
        return self.async_show_form(
            step_id="select_device",
            data_schema=_device_schema(self._devices),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(_config_entry):
        return TholzFairlandOptionsFlowHandler()

    async def async_step_reauth(self, _entry_data: dict[str, str]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, str] | None = None):
        entry = self._get_reauth_entry()
        return await self._async_existing_credentials_step(
            "reauth", entry, "reauth_confirm", _reauth_schema, user_input
        )

    async def async_step_reconfigure(self, user_input: dict[str, str] | None = None):
        """Validate and replace the credentials used by automatic recovery."""
        entry = self._get_reconfigure_entry()
        return await self._async_existing_credentials_step(
            "reconfigure", entry, "reconfigure", _reconfigure_schema, user_input
        )

    async def _async_existing_credentials_step(
        self, flow_kind, entry, step_id, schema_factory, user_input
    ):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._flow_kind = flow_kind
            self._target_entry = entry
            self._reauth_client_secret = None
            self._reauth_username = user_input[CONF_COGNITO_USERNAME_KEY]
            self._reauth_password = user_input[CONF_COGNITO_PASSWORD_KEY]
            outcome, error = await self._run_srp_login()
            if self._removed:
                return self.async_abort(reason="reauth_unsuccessful")
            if error is not None:
                errors["base"] = error
            elif isinstance(outcome, ChallengeRequired):
                self._challenge_state = outcome.state
                return await self.async_step_challenge()
            elif isinstance(outcome, AuthSuccess):
                result, error = await self._finish_existing_auth(entry, outcome)
                if result is not None:
                    return result
                errors["base"] = error or ERR_CANNOT_CONNECT
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema_factory({**entry.data, **entry.options}),
            errors=errors,
        )

    async def async_step_challenge(self, user_input: dict[str, str] | None = None):
        if self._challenge_state is None:
            return self.async_abort(reason="configuration_error")
        errors: dict[str, str] = {}
        challenge_name = self._challenge_state.challenge_name
        if user_input is not None:
            response = user_input[CONF_CHALLENGE_RESPONSE_KEY]
            was_new_password = (
                self._challenge_state.challenge_name == CHALLENGE_NEW_PASSWORD_REQUIRED
            )
            outcome, error = await self._run_challenge_response(response)
            if self._removed:
                return self.async_abort(reason="reauth_unsuccessful")
            if error is not None:
                errors["base"] = error
            else:
                if was_new_password:
                    # Automatic recovery must retain the replacement password.
                    self._reauth_password = response
                if isinstance(outcome, ChallengeRequired):
                    self._challenge_state = outcome.state
                    challenge_name = outcome.state.challenge_name
                elif isinstance(outcome, AuthSuccess):
                    if self._flow_kind == "user":
                        result, error = await self._finish_user_auth(outcome)
                    elif self._target_entry is not None:
                        result, error = await self._finish_existing_auth(
                            self._target_entry, outcome
                        )
                    else:
                        result, error = None, ERR_CONFIGURATION
                    if result is not None:
                        return result
                    errors["base"] = error or ERR_CANNOT_CONNECT
        return self.async_show_form(
            step_id="challenge",
            data_schema=_challenge_schema(challenge_name),
            errors=errors,
        )

    async def _run_srp_login(self):
        authenticator = self._get_authenticator()
        assert self._reauth_username is not None  # noqa: S101
        assert self._reauth_password is not None  # noqa: S101
        try:
            outcome = await self.hass.async_add_executor_job(
                authenticator.login_srp,
                self._reauth_username,
                self._reauth_password,
                self._reauth_client_secret,
            )
        except CognitoChallengeRequired:
            return None, ERR_UNSUPPORTED_CHALLENGE
        except Exception as exc:
            return None, _sanitized_error(exc)
        return outcome, None

    async def _run_challenge_response(self, response: str):
        authenticator = self._get_authenticator()
        state = self._challenge_state
        assert state is not None  # noqa: S101
        try:
            outcome = await self.hass.async_add_executor_job(
                authenticator.respond_to_challenge,
                state,
                response,
                self._reauth_client_secret,
            )
        except CognitoChallengeRequired:
            return None, ERR_UNSUPPORTED_CHALLENGE
        except Exception as exc:
            return None, _sanitized_error(exc)
        return outcome, None

    async def _verify_refresh(self, outcome: AuthSuccess) -> tuple[str | None, str]:
        """Prove the SRP refresh token works before persisting it."""
        try:
            verified = await self.hass.async_add_executor_job(
                self._get_authenticator().refresh,
                outcome.tokens.refresh_token,
                outcome.subject,
                self._reauth_client_secret,
            )
        except CognitoTransientError:
            return None, outcome.tokens.refresh_token
        except Exception:
            return ERR_WRONG_ACCOUNT, ""
        return None, (verified.refresh_token or outcome.tokens.refresh_token)

    async def _finish_user_auth(  # noqa: PLR0911 - explicit flow exits are safer
        self, outcome: AuthSuccess
    ):
        verify_error, token = await self._verify_refresh(outcome)
        if self._removed:
            return self.async_abort(reason="user_cancelled"), None
        if verify_error is not None:
            return None, verify_error
        try:
            devices = await _discover_devices(self.hass, outcome.tokens.id_token)
        except ValueError as exc:
            if self._removed:
                return self.async_abort(reason="user_cancelled"), None
            return None, str(exc)
        if self._removed:
            return self.async_abort(reason="user_cancelled"), None
        if not devices:
            return None, ERR_NO_DEVICES
        self._pending_success = outcome
        self._verified_refresh_token = token
        self._devices = devices
        if len(devices) == 1:
            return await self._create_user_entry(next(iter(devices)))
        return (
            self.async_show_form(
                step_id="select_device", data_schema=_device_schema(devices), errors={}
            ),
            None,
        )

    async def _create_user_entry(self, thing_id: str):
        if self._removed:
            return self.async_abort(reason="user_cancelled"), None
        outcome = self._pending_success
        token = self._verified_refresh_token
        if outcome is None or token is None:
            return None, ERR_CONFIGURATION
        data = _build_entry_data(
            self._pending_user_input,
            thing_id=thing_id,
            user_sub=outcome.subject,
            refresh_token=token,
        )
        # Reserve/check identity before another cloud request. Duplicate setup
        # attempts must not consume rate limits or MFA challenges unnecessarily.
        await self.async_set_unique_id(thing_id)
        if self._removed:
            return self.async_abort(reason="user_cancelled"), None
        self._abort_if_unique_id_configured()
        try:
            validated_token = await _validate_cloud(self.hass, data)
        except ValueError as exc:
            return None, str(exc)
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = validated_token
        data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = validated_token
        if self._removed:
            return self.async_abort(reason="user_cancelled"), None
        title = data[CONF_NAME_KEY]
        self._scrub_flow_secrets()
        return self.async_create_entry(title=title, data=data), None

    async def _finish_existing_auth(self, entry, outcome: AuthSuccess):
        if subject_error := _existing_subject_error(entry, outcome):
            return None, subject_error
        verify_error, token = await self._verify_refresh(outcome)
        if verify_error is not None:
            return None, verify_error
        if self._removed:
            return self.async_abort(reason="reauth_unsuccessful"), None
        data = dict(entry.data)
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = token
        data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = token
        data[CONF_COGNITO_USER_SUB_KEY] = outcome.subject
        data[CONF_COGNITO_CLIENT_SECRET_KEY] = ""
        data[CONF_COGNITO_USERNAME_KEY] = self._reauth_username
        data[CONF_RECOVERY_MODE_KEY] = RECOVERY_MODE_AUTOMATIC
        data[CONF_COGNITO_PASSWORD_KEY] = self._reauth_password
        try:
            validated_token = await _validate_cloud(self.hass, data)
        except ValueError as exc:
            return None, str(exc)
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = validated_token
        data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = validated_token
        if self._removed:
            return self.async_abort(reason="reauth_unsuccessful"), None
        options = dict(entry.options)
        options.pop(CONF_COGNITO_REFRESH_TOKEN_KEY, None)
        options.pop(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY, None)
        self._scrub_flow_secrets()
        return self.async_update_reload_and_abort(
            entry, data=data, options=options
        ), None

    def _get_authenticator(self) -> CognitoAuthenticator:
        if self._authenticator is None:
            self._authenticator = CognitoAuthenticator(
                REGION, USER_POOL_ID, USER_POOL_CLIENT_ID
            )
        return self._authenticator

    def _scrub_flow_secrets(self) -> None:
        """Remove transient plaintext and challenge material from flow memory."""
        self._challenge_state = None
        self._reauth_password = None
        self._pending_success = None
        self._verified_refresh_token = None
        self._pending_user_input = {}
        self._devices = {}


def _build_entry_data(
    user_input: dict[str, Any], *, thing_id: str, user_sub: str, refresh_token: str
) -> dict[str, Any]:
    """Assemble config-entry data after account and device validation."""
    data: dict[str, Any] = {
        CONF_NAME_KEY: user_input[CONF_NAME_KEY],
        CONF_THING_ID_KEY: thing_id,
        CONF_COGNITO_USER_SUB_KEY: user_sub,
        CONF_COGNITO_CLIENT_SECRET_KEY: "",
        CONF_COGNITO_REFRESH_TOKEN_KEY: refresh_token,
        CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: refresh_token,
        CONF_COGNITO_USERNAME_KEY: user_input[CONF_COGNITO_USERNAME_KEY],
        CONF_COGNITO_PASSWORD_KEY: user_input[CONF_COGNITO_PASSWORD_KEY],
        CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_AUTOMATIC,
    }
    if user_input.get(CONF_POLLING_INTERVAL_KEY) is not None:
        data[CONF_POLLING_INTERVAL_KEY] = user_input[CONF_POLLING_INTERVAL_KEY]
    return data


class TholzFairlandOptionsFlowHandler(config_entries.OptionsFlow):
    """Edit the polling frequency. Credentials are changed via reauth/reconfigure."""

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            options = {CONF_POLLING_INTERVAL_KEY: user_input[CONF_POLLING_INTERVAL_KEY]}
            # No update listener is registered; reload explicitly so the new
            # polling interval takes effect.
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=options
            )
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current)
        )
