"""Configuration flow for the Tholz Fairland cloud integration."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .cloud.client import (
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
    RECOVERY_MODE_INTERACTIVE,
)

ERR_CANNOT_CONNECT = "cannot_connect"
ERR_INVALID_TOKEN = "invalid_token"  # noqa: S105
ERR_INVALID_AUTH = "invalid_auth"
ERR_UNSUPPORTED_CHALLENGE = "unsupported_challenge"
ERR_CHALLENGE_FAILED = "challenge_failed"
ERR_CONFIGURATION = "configuration_error"
ERR_MISSING_PASSWORD = "missing_password"  # noqa: S105
ERR_WRONG_ACCOUNT = "wrong_account"

CONF_CHALLENGE_RESPONSE_KEY = "challenge_response"

_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[RECOVERY_MODE_AUTOMATIC, RECOVERY_MODE_INTERACTIVE],
        translation_key="recovery_mode",
    )
)


def _suggest(value: str) -> dict[str, str]:
    """Prefill a non-secret field without echoing a secret as a form default."""
    return {"suggested_value": value}


def _cloud_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME_KEY, default=defaults.get(CONF_NAME_KEY, "Fairland")
            ): str,
            vol.Required(
                CONF_COGNITO_USER_SUB_KEY,
                default=defaults.get(CONF_COGNITO_USER_SUB_KEY, ""),
            ): str,
            # The current public Cognito app client has no client secret. Keep
            # the legacy field optional only so old/manual entries can migrate.
            vol.Optional(CONF_COGNITO_CLIENT_SECRET_KEY): _PASSWORD_SELECTOR,
            vol.Required(CONF_COGNITO_REFRESH_TOKEN_KEY): _PASSWORD_SELECTOR,
            vol.Required(
                CONF_THING_ID_KEY, default=defaults.get(CONF_THING_ID_KEY, "")
            ): str,
            vol.Optional(
                CONF_COGNITO_USERNAME_KEY,
                default=defaults.get(CONF_COGNITO_USERNAME_KEY, ""),
            ): str,
            vol.Optional(
                CONF_RECOVERY_MODE_KEY,
                default=defaults.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE),
            ): _MODE_SELECTOR,
            vol.Optional(CONF_COGNITO_PASSWORD_KEY): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_POLLING_INTERVAL_KEY,
                default=defaults.get(
                    CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
                ),
            ): vol.All(int, vol.Range(min=10, max=300)),
        }
    )


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
            vol.Optional(
                CONF_RECOVERY_MODE_KEY,
                default=defaults.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE),
            ): _MODE_SELECTOR,
        }
    )


def _reconfigure_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_RECOVERY_MODE_KEY,
                default=defaults.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE),
            ): _MODE_SELECTOR,
            vol.Optional(CONF_COGNITO_PASSWORD_KEY): _PASSWORD_SELECTOR,
        }
    )


def _challenge_schema(challenge_name: str | None = None) -> vol.Schema:
    # A new password is masked; a one-time code is shown as the user types it.
    field: Any = (
        _PASSWORD_SELECTOR if challenge_name == CHALLENGE_NEW_PASSWORD_REQUIRED else str
    )
    return vol.Schema({vol.Required(CONF_CHALLENGE_RESPONSE_KEY): field})


async def _validate_cloud(hass, values: dict) -> None:
    """Authenticate and read state without sending a device command."""
    client = FairlandCloudClient(
        thing_id=values[CONF_THING_ID_KEY],
        user_sub=values[CONF_COGNITO_USER_SUB_KEY],
        client_secret=values.get(CONF_COGNITO_CLIENT_SECRET_KEY),
        refresh_token=values[CONF_COGNITO_REFRESH_TOKEN_KEY],
    )
    try:
        await hass.async_add_executor_job(client.get_status)
    except FairlandCloudAuthError as exc:
        raise ValueError(ERR_INVALID_TOKEN) from exc
    except Exception as exc:
        raise ValueError(ERR_CANNOT_CONNECT) from exc
    finally:
        await hass.async_add_executor_job(client.close)


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


class TholzFairlandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a cloud-connected Fairland heat pump."""

    VERSION = CONFIG_ENTRY_VERSION
    MINOR_VERSION = CONFIG_ENTRY_MINOR_VERSION

    def __init__(self) -> None:
        # Everything below lives only in flow memory. The Cognito challenge
        # ``Session`` and the password are never written to a config entry.
        self._authenticator: CognitoAuthenticator | None = None
        self._challenge_state: ChallengeState | None = None
        self._reauth_username: str | None = None
        self._reauth_password: str | None = None
        self._reauth_mode: str = RECOVERY_MODE_INTERACTIVE
        self._reauth_client_secret: str | None = None
        self._removed = False

    @callback
    def async_remove(self) -> None:
        """
        Scrub challenge/password state when the flow is removed.

        Home Assistant calls this hook synchronously and never awaits it, so it
        must not be a coroutine; otherwise the scrub and the cancellation flag
        checked after every executor await would silently never run.
        """
        self._removed = True
        self._challenge_state = None
        self._reauth_password = None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get(
                CONF_RECOVERY_MODE_KEY
            ) == RECOVERY_MODE_AUTOMATIC and not user_input.get(
                CONF_COGNITO_PASSWORD_KEY
            ):
                errors["base"] = ERR_MISSING_PASSWORD
            else:
                # Claim the device identity before any network call so a
                # duplicate cannot be created by a slow validation.
                await self.async_set_unique_id(user_input[CONF_THING_ID_KEY])
                self._abort_if_unique_id_configured()
                try:
                    await _validate_cloud(self.hass, user_input)
                except ValueError as exc:
                    errors["base"] = str(exc)
                except Exception:
                    errors["base"] = ERR_CANNOT_CONNECT
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_NAME_KEY],
                        data=_build_entry_data(user_input),
                    )
        return self.async_show_form(
            step_id="user", data_schema=_cloud_schema(user_input), errors=errors
        )

    @staticmethod
    def async_get_options_flow(_config_entry):
        return TholzFairlandOptionsFlowHandler()

    async def async_step_reauth(self, _entry_data: dict[str, str]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, str] | None = None):
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            # Tholz 4.1.0 moved to a public Cognito app client. Never submit the
            # obsolete secret retained by a pre-1.3 entry.
            self._reauth_client_secret = None
            self._reauth_username = user_input[CONF_COGNITO_USERNAME_KEY]
            self._reauth_password = user_input[CONF_COGNITO_PASSWORD_KEY]
            self._reauth_mode = user_input.get(
                CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE
            )
            outcome, error = await self._run_srp_login()
            if self._removed:
                return self.async_abort(reason="reauth_unsuccessful")
            if error is not None:
                errors["base"] = error
            elif isinstance(outcome, ChallengeRequired):
                self._challenge_state = outcome.state
                return await self.async_step_challenge()
            elif isinstance(outcome, AuthSuccess):
                verify_error, token = await self._verify_subject(outcome)
                if verify_error is None:
                    return self._finish_reauth(entry, outcome, token)
                errors["base"] = verify_error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema({**entry.data, **entry.options}),
            errors=errors,
        )

    async def async_step_challenge(self, user_input: dict[str, str] | None = None):
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        challenge_name = (
            self._challenge_state.challenge_name if self._challenge_state else None
        )
        if user_input is not None and self._challenge_state is not None:
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
                    # The recovered password is the new one, not the old input.
                    # Capture it now even if another challenge (e.g. MFA) follows,
                    # otherwise automatic mode would store the superseded password.
                    self._reauth_password = response
                if isinstance(outcome, ChallengeRequired):
                    self._challenge_state = outcome.state
                    challenge_name = outcome.state.challenge_name
                elif isinstance(outcome, AuthSuccess):
                    verify_error, token = await self._verify_subject(outcome)
                    if verify_error is None:
                        return self._finish_reauth(entry, outcome, token)
                    errors["base"] = verify_error
        return self.async_show_form(
            step_id="challenge",
            data_schema=_challenge_schema(challenge_name),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, str] | None = None):
        """Switch recovery mode (and password storage) on an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            mode = user_input.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE)
            password = user_input.get(CONF_COGNITO_PASSWORD_KEY)
            stored_password = entry.data.get(CONF_COGNITO_PASSWORD_KEY)
            if mode == RECOVERY_MODE_AUTOMATIC and not (password or stored_password):
                errors["base"] = ERR_MISSING_PASSWORD
            else:
                data = dict(entry.data)
                data[CONF_RECOVERY_MODE_KEY] = mode
                if mode == RECOVERY_MODE_AUTOMATIC:
                    if password:
                        data[CONF_COGNITO_PASSWORD_KEY] = password
                else:
                    data.pop(CONF_COGNITO_PASSWORD_KEY, None)
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema({**entry.data, **entry.options}),
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

    async def _verify_subject(self, outcome: AuthSuccess) -> tuple[str | None, str]:
        """
        Prove the recovered token refreshes with its subject before commit.

        Returns ``(error, token)``. A subject Cognito will not accept for the
        refresh grant would loop after reload, so a rejected verification blocks
        the write. A transient failure trusts the SRP result rather than
        stranding the user. If the verification refresh rotated the token, that
        rotated token — not the now-superseded SRP token — is the one to keep.
        """
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

    def _get_authenticator(self) -> CognitoAuthenticator:
        if self._authenticator is None:
            self._authenticator = CognitoAuthenticator(
                REGION, USER_POOL_ID, USER_POOL_CLIENT_ID
            )
        return self._authenticator

    def _finish_reauth(self, entry, outcome: AuthSuccess, token: str):
        if self._removed:
            # Cancelled while the verification refresh was in flight: write nothing.
            return self.async_abort(reason="reauth_unsuccessful")
        data = dict(entry.data)
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = token
        data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = token
        data[CONF_COGNITO_USER_SUB_KEY] = outcome.subject
        data[CONF_COGNITO_CLIENT_SECRET_KEY] = ""
        data[CONF_COGNITO_USERNAME_KEY] = self._reauth_username
        data[CONF_RECOVERY_MODE_KEY] = self._reauth_mode
        if self._reauth_mode == RECOVERY_MODE_AUTOMATIC:
            data[CONF_COGNITO_PASSWORD_KEY] = self._reauth_password
        else:
            data.pop(CONF_COGNITO_PASSWORD_KEY, None)
        options = dict(entry.options)
        options.pop(CONF_COGNITO_REFRESH_TOKEN_KEY, None)
        options.pop(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY, None)
        # Scrub challenge and password material from flow memory before return.
        self._challenge_state = None
        self._reauth_password = None
        return self.async_update_reload_and_abort(entry, data=data, options=options)


def _build_entry_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Assemble config-entry data for a new entry from validated input."""
    token = user_input[CONF_COGNITO_REFRESH_TOKEN_KEY]
    mode = user_input.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE)
    data: dict[str, Any] = {
        CONF_NAME_KEY: user_input[CONF_NAME_KEY],
        CONF_THING_ID_KEY: user_input[CONF_THING_ID_KEY],
        CONF_COGNITO_USER_SUB_KEY: user_input[CONF_COGNITO_USER_SUB_KEY],
        CONF_COGNITO_CLIENT_SECRET_KEY: "",
        CONF_COGNITO_REFRESH_TOKEN_KEY: token,
        CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: token,
        CONF_RECOVERY_MODE_KEY: mode,
    }
    if user_input.get(CONF_COGNITO_USERNAME_KEY):
        data[CONF_COGNITO_USERNAME_KEY] = user_input[CONF_COGNITO_USERNAME_KEY]
    if user_input.get(CONF_POLLING_INTERVAL_KEY) is not None:
        data[CONF_POLLING_INTERVAL_KEY] = user_input[CONF_POLLING_INTERVAL_KEY]
    if mode == RECOVERY_MODE_AUTOMATIC and user_input.get(CONF_COGNITO_PASSWORD_KEY):
        data[CONF_COGNITO_PASSWORD_KEY] = user_input[CONF_COGNITO_PASSWORD_KEY]
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
