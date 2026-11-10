"""AWS Cognito and IoT-shadow transport for Fairland heat pumps."""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
import urllib.parse
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests
from awscrt import mqtt
from awsiot import mqtt_connection_builder

from .auth_manager import FairlandAuthManager, RecoveryMode
from .temperature_policy import (
    MAX_SETPOINT,
    MIN_SETPOINT,
    FairlandTemperatureError,
    temperature_policy,
)
from .cognito_auth import (
    CognitoAuthenticator,
    CognitoAuthError,
    CognitoChallengeRequired,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

REGION = "us-east-1"
USER_POOL_ID = "us-east-1_JA4N5tQGy"
USER_POOL_CLIENT_ID = "4crf9di2so4tfe0cjn7lndci3j"
API_BASE_URL = "https://n1owhaiarj.execute-api.us-east-1.amazonaws.com/master"
IOT_AUTHORIZER_URL = f"{API_BASE_URL}/iot/authorizer"
METRICS_URL = "https://n1owhaiarj.execute-api.us-east-1.amazonaws.com/master/metrics"
IOT_ENDPOINT = "a20gdo93r3ncps-ats.iot.us-east-1.amazonaws.com"
METRICS_WINDOW_SECONDS = 24 * 60 * 60
METRICS_CACHE_SECONDS = 5 * 60
DEFAULT_CONVERGENCE_TIMEOUT = 45.0
SCHEDULE_SHADOW_NAME = "heat0Schedule"
SCHEDULE_EVENT_COUNT = 5
SCHEDULE_REPEAT_MASK_MAX = 127
SCHEDULE_HOUR_MAX = 23
SCHEDULE_MINUTE_MAX = 59

MUTABLE_HEAT_FIELDS = frozenset({"opMode", "sp", "fanMode"})
VALID_OPERATION_MODES = frozenset({0, 2, 4, 5})
VALID_FAN_MODES = frozenset({0, 1})
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

ERR_WRITE_PATH = "cloud writes must target heatings.heat0"
ERR_INTEGER_FIELD = "writable Fairland fields must be integers"
ERR_OPERATION_MODE = "unsupported Fairland operation mode"
ERR_FAN_MODE = "unsupported Fairland fan mode"
ERR_SETPOINT = "setpoint must be between 12 and 40 °C"
ERR_NO_CHANGES = "payload contains no changed writable fields"
ERR_COGNITO_REFRESH = "unable to refresh Cognito session"
ERR_IOT_AUTHORIZER = "unable to obtain the AWS IoT authorizer"
ERR_MQTT_CONNECT = "unable to connect to AWS IoT"
ERR_GET_PUBLISH = "shadow GET publish failed"
ERR_GET_TIMEOUT = "shadow GET timed out"
ERR_GET_EMPTY = "shadow GET returned no document"
ERR_NO_REPORTED = "shadow has no reported state"
ERR_NO_HEAT = "shadow has no reported heatings.heat0"
ERR_UPDATE_PUBLISH = "shadow UPDATE publish failed"
ERR_UPDATE_ACCEPT = "shadow UPDATE was not accepted"
ERR_UPDATE_CONVERGENCE = "shadow UPDATE accepted but reported state did not converge"
ERR_MISSING_BASELINE = "reported state is missing a writable baseline field"
ERR_UNSAFE_BASELINE = "reported state cannot provide a safe rollback baseline"
ERR_ROLLBACK_FAILED = "shadow UPDATE failed and baseline restoration also failed"
ERR_METRICS_RESPONSE = "metrics API returned an invalid response"
ERR_SCHEDULE_RESPONSE = "heat0Schedule returned an invalid response"


class FairlandCloudError(RuntimeError):
    """Base cloud transport error."""


class FairlandCloudAuthError(FairlandCloudError):
    """Authentication or authorization failed."""


class FairlandCloudProtocolError(FairlandCloudError):
    """The cloud response did not match the verified protocol."""


@dataclass(frozen=True, slots=True)
class IotAuthorizer:
    """Short-lived custom-authorizer material returned by the Tholz API."""

    name: str
    signature: str = field(repr=False)


def extract_mutable_changes(
    payload: dict[str, Any], current: dict[str, Any] | None
) -> dict[str, int]:
    """Return only changed, writable fields from a legacy-shaped payload."""
    try:
        requested = payload["heatings"]["heat0"]
    except (KeyError, TypeError) as exc:
        raise FairlandCloudProtocolError(ERR_WRITE_PATH) from exc

    if not isinstance(requested, dict):
        raise FairlandCloudProtocolError(ERR_WRITE_PATH)
    current_heat = ((current or {}).get("heatings") or {}).get("heat0") or {}
    changes: dict[str, int] = {}
    for key in MUTABLE_HEAT_FIELDS:
        if key not in requested:
            continue
        value = requested[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise FairlandCloudProtocolError(ERR_INTEGER_FIELD)
        if value == current_heat.get(key):
            continue
        changes[key] = value

    if "opMode" in changes and changes["opMode"] not in VALID_OPERATION_MODES:
        raise FairlandCloudProtocolError(ERR_OPERATION_MODE)
    if "fanMode" in changes and changes["fanMode"] not in VALID_FAN_MODES:
        raise FairlandCloudProtocolError(ERR_FAN_MODE)
    if "sp" in changes and not MIN_SETPOINT <= changes["sp"] <= MAX_SETPOINT:
        raise FairlandCloudProtocolError(ERR_SETPOINT)
    if not changes:
        raise FairlandCloudProtocolError(ERR_NO_CHANGES)
    return changes


def build_shadow_update(
    changes: dict[str, int], client_token: str | None = None
) -> str:
    """Build the exact nested classic-shadow payload consumed by Fairland."""
    document: dict[str, Any] = {"state": {"desired": {"heatings": {"heat0": changes}}}}
    if client_token is not None:
        document["clientToken"] = client_token
    return json.dumps(document, separators=(",", ":"))


def summarize_metrics_document(
    document: dict[str, Any], now_ms: int | None = None
) -> dict[str, float]:
    """Summarize the verified heat0 active-time table over a rolling 24 hours."""
    now_ms = now_ms or int(time.time() * 1000)
    cutoff_ms = now_ms - (METRICS_WINDOW_SECONDS * 1000)
    table = document.get("metrics", {}).get("activeTime", {})
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE)
    indexes = {name: index for index, name in enumerate(columns)}
    required = {"key", "time", "active"}
    if not required.issubset(indexes):
        raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE)

    active_ms = 0
    for row in rows:
        if not isinstance(row, list) or len(row) < len(columns):
            continue
        if row[indexes["key"]] != "heat0" or row[indexes["time"]] < cutoff_ms:
            continue
        value = row[indexes["active"]]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            active_ms += max(0, value)

    window_ms = METRICS_WINDOW_SECONDS * 1000
    return {
        "heatingActiveSeconds24h": round(active_ms / 1000, 1),
        "heatingDutyCycle24h": round((active_ms / window_ms) * 100, 2),
    }


def normalize_schedule_document(document: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the five read-only Fairland schedule slots."""
    reported = document.get("state", {}).get("reported")
    if not isinstance(reported, dict):
        raise FairlandCloudProtocolError(ERR_SCHEDULE_RESPONSE)

    schedule: dict[str, Any] = {}
    for index in range(SCHEDULE_EVENT_COUNT):
        key = f"event{index}"
        event = reported.get(key)
        if not isinstance(event, dict):
            raise FairlandCloudProtocolError(ERR_SCHEDULE_RESPONSE)
        active = event.get("active")
        turn_on = event.get("turnOn")
        turn_off = event.get("turnOff")
        repeat = event.get("rept")
        if (
            not isinstance(active, bool)
            or not isinstance(turn_on, dict)
            or not isinstance(turn_off, dict)
            or isinstance(repeat, bool)
            or not isinstance(repeat, int)
            or not 0 <= repeat <= SCHEDULE_REPEAT_MASK_MAX
        ):
            raise FairlandCloudProtocolError(ERR_SCHEDULE_RESPONSE)
        for time_value in (turn_on, turn_off):
            hour = time_value.get("hr")
            minute = time_value.get("min")
            if (
                isinstance(hour, bool)
                or not isinstance(hour, int)
                or not 0 <= hour <= SCHEDULE_HOUR_MAX
                or isinstance(minute, bool)
                or not isinstance(minute, int)
                or not 0 <= minute <= SCHEDULE_MINUTE_MAX
            ):
                raise FairlandCloudProtocolError(ERR_SCHEDULE_RESPONSE)
        schedule[key] = {
            "active": active,
            "turnOn": {"hr": turn_on["hr"], "min": turn_on["min"]},
            "turnOff": {"hr": turn_off["hr"], "min": turn_off["min"]},
            "rept": repeat,
        }
    return schedule


class FairlandCloudClient:
    """Synchronous client compatible with the integration's existing manager."""

    def __init__(
        self,
        thing_id: str,
        user_sub: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        initial_refresh_token: str | None = None,
        timeout: float = 15.0,
        convergence_timeout: float = DEFAULT_CONVERGENCE_TIMEOUT,
        *,
        auth_manager: FairlandAuthManager | None = None,
    ) -> None:
        self.thing_id = thing_id
        self._timeout = timeout
        self._convergence_timeout = convergence_timeout

        # Token acquisition (refresh -> recovery seed -> SRP login) is owned by
        # the auth manager. When one is not injected, build a refresh-only
        # manager from the legacy credential arguments so existing callers and
        # tests keep working unchanged.
        if auth_manager is None:
            authenticator = CognitoAuthenticator(
                REGION, USER_POOL_ID, USER_POOL_CLIENT_ID
            )
            auth_manager = FairlandAuthManager(
                authenticator,
                user_sub=user_sub or "",
                client_secret=client_secret,
                current_token=refresh_token or "",
                recovery_token=initial_refresh_token,
                mode=RecoveryMode.INTERACTIVE,
            )
        self._auth = auth_manager

        self._connection_expiry = 0.0
        self._connection = None
        self._connecting_connection = None
        self._connection_interrupted = False
        self._interrupted_connection_ids: set[int] = set()
        self._connection_state_lock = threading.Lock()
        self._lock = threading.RLock()

        self._get_event = threading.Event()
        self._update_event = threading.Event()
        self._schedule_get_event = threading.Event()
        self._get_response: dict[str, Any] | None = None
        self._update_response: dict[str, Any] | None = None
        self._schedule_get_response: dict[str, Any] | None = None
        self._get_client_token: str | None = None
        self._update_client_token: str | None = None
        self._schedule_get_client_token: str | None = None
        self._last_reported: dict[str, Any] | None = None
        self._last_schedule: dict[str, Any] | None = None
        self._last_schedule_version: int | None = None
        self._last_schedule_timestamp: int | None = None
        self._metrics_summary: dict[str, float] | None = None
        self._metrics_updated_at = 0.0
        self._state_change_callback: Callable[[], None] | None = None

        shadow = f"$aws/things/{self.thing_id}/shadow"
        self._get_topic = f"{shadow}/get"
        self._get_accepted_topic = f"{shadow}/get/accepted"
        self._update_topic = f"{shadow}/update"
        self._update_accepted_topic = f"{shadow}/update/accepted"
        schedule_shadow = f"{shadow}/name/{SCHEDULE_SHADOW_NAME}"
        self._schedule_get_topic = f"{schedule_shadow}/get"
        self._schedule_get_accepted_topic = f"{schedule_shadow}/get/accepted"
        self._schedule_update_accepted_topic = f"{schedule_shadow}/update/accepted"

    def _id_token(self) -> str:
        """
        Return a valid Cognito id token, translating auth errors for setup.

        The auth manager owns the recovery chain and any reauth signalling; the
        client only maps its typed failures onto the transport errors the
        integration already distinguishes (auth failure vs. retry-later).
        """
        try:
            return self._auth.get_valid_id_token()
        except (
            CognitoCredentialRejected,
            CognitoChallengeRequired,
            CognitoConfigurationError,
        ) as exc:
            raise FairlandCloudAuthError(ERR_COGNITO_REFRESH) from exc
        except CognitoTransientError as exc:
            # Retry-later: surfaced as a non-auth error so setup maps it to
            # ConfigEntryNotReady rather than a reauthentication prompt.
            raise FairlandCloudError(ERR_COGNITO_REFRESH) from exc
        except CognitoProtocolError as exc:
            raise FairlandCloudProtocolError(ERR_COGNITO_REFRESH) from exc
        except CognitoAuthError as exc:
            raise FairlandCloudAuthError(ERR_COGNITO_REFRESH) from exc

    @property
    def id_token_expiry(self) -> float | None:
        """Expiry (UTC epoch) of the currently valid Cognito id token, if any."""
        return self._auth.id_token_expiry

    def _fetch_iot_authorizer(self, id_token: str) -> IotAuthorizer:
        """Fetch the custom-authorizer name/signature used by Tholz 4.1+."""
        try:
            response = requests.get(
                IOT_AUTHORIZER_URL,
                headers={"Authorization": id_token},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise FairlandCloudError(ERR_IOT_AUTHORIZER) from exc
        if response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}:
            self._auth.report_authorization_rejected()
            raise FairlandCloudAuthError(ERR_IOT_AUTHORIZER)
        if response.status_code != HTTP_OK:
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER)
        try:
            document = response.json()
        except requests.JSONDecodeError as exc:
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER) from exc
        if not isinstance(document, dict):
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER)
        name = document.get("authorizer")
        signature = document.get("signature")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(signature, str)
            or not signature
        ):
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER)
        return IotAuthorizer(name=name, signature=signature)

    def _mqtt_query(
        self, id_token: str, authorizer: IotAuthorizer, expires_at: float
    ) -> str:
        """Build the exact query-string protocol used by Tholz 4.1 build 93."""
        disconnect_after = max(1, int(expires_at - time.time()))
        return urllib.parse.urlencode(
            {
                "x-amz-customauthorizer-name": authorizer.name,
                "x-amz-customauthorizer-signature": authorizer.signature,
                "token": id_token,
                "thingIds": self.thing_id,
                "disconnectAfterInSeconds": str(disconnect_after),
            },
            quote_via=urllib.parse.quote,
        )

    def _on_interrupted(self, connection, error, **_kwargs) -> None:
        with self._connection_state_lock:
            if (
                connection is self._connection
                or connection is self._connecting_connection
            ):
                self._interrupted_connection_ids.add(id(connection))
            if connection is self._connection:
                self._connection_interrupted = True
                self._connection_expiry = 0.0
        # The CRT client automatically reconnects with the same WebSocket
        # transform. That would replay an expiring ID token and authorizer
        # signature, and clean_session=True would lose subscriptions. Stop that
        # reconnect loop; the next serialized operation creates a fresh
        # connection, handshake, client ID and all four subscriptions.
        with suppress(Exception):
            connection.disconnect()
        error_name = type(error).__name__
        _LOGGER.warning(
            "Tholz Fairland cloud MQTT connection interrupted (%s); "
            "a fresh authenticated connection will be created",
            error_name,
        )

    def _on_message(self, topic: str, payload: bytes, **_kwargs) -> None:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _LOGGER.warning("Tholz Fairland cloud returned an invalid MQTT payload")
            return
        reported = decoded.get("state", {}).get("reported")
        if (
            topic == self._update_accepted_topic
            and isinstance(reported, dict)
            and self._state_change_callback is not None
        ):
            self._state_change_callback()
        if (
            topic == self._schedule_update_accepted_topic
            and isinstance(reported, dict)
            and self._state_change_callback is not None
        ):
            self._state_change_callback()
        if (
            topic == self._get_accepted_topic
            and decoded.get("clientToken") == self._get_client_token
        ):
            self._get_response = decoded
            self._get_event.set()
        elif (
            topic == self._update_accepted_topic
            and decoded.get("clientToken") == self._update_client_token
        ):
            self._update_response = decoded
            self._update_event.set()
        elif (
            topic == self._schedule_get_accepted_topic
            and decoded.get("clientToken") == self._schedule_get_client_token
        ):
            self._schedule_get_response = decoded
            self._schedule_get_event.set()

    def set_state_change_callback(self, callback: Callable[[], None] | None) -> None:
        """Notify the manager when any client reports a shadow state change."""
        self._state_change_callback = callback

    def _disconnect(self) -> None:
        with self._connection_state_lock:
            connection, self._connection = self._connection, None
            self._connection_interrupted = False
            self._connection_expiry = 0.0
            if connection is not None:
                self._interrupted_connection_ids.discard(id(connection))
        if connection is not None:
            try:
                connection.disconnect().result(timeout=min(self._timeout, 8))
            except Exception as exc:  # best-effort shutdown
                _LOGGER.debug("Tholz Fairland cloud MQTT disconnect failed: %s", exc)

    def close(self) -> None:
        """Close the MQTT connection."""
        with self._lock:
            self._disconnect()

    def _activate_connection(self, connection, expires_at: float) -> None:
        """Publish a fully subscribed connection only if no callback invalidated it."""
        with self._connection_state_lock:
            if (
                self._connecting_connection is not connection
                or id(connection) in self._interrupted_connection_ids
            ):
                raise FairlandCloudProtocolError(ERR_MQTT_CONNECT)
            self._connection = connection
            self._connection_expiry = expires_at
            self._connection_interrupted = False
            self._connecting_connection = None
            self._interrupted_connection_ids.discard(id(connection))

    def _ensure_connection(self) -> None:
        with self._connection_state_lock:
            if (
                self._connection is not None
                and not self._connection_interrupted
                and self._connection_expiry > time.time() + 120
            ):
                return

        self._disconnect()
        id_token = self._id_token()
        expires_at = self.id_token_expiry
        if expires_at is None or expires_at <= time.time() + 120:
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER)
        authorizer = self._fetch_iot_authorizer(id_token)
        query = self._mqtt_query(id_token, authorizer, expires_at)

        def _transform_handshake(transform_args, **_kwargs) -> None:
            try:
                transform_args.http_request.path = f"/mqtt?{query}"
                transform_args.set_done()
            except Exception as exc:
                transform_args.set_done(exc)

        user_sub = self._auth.user_sub
        if not user_sub:
            raise FairlandCloudProtocolError(ERR_IOT_AUTHORIZER)
        connection = mqtt_connection_builder.websockets_with_custom_handshake(
            websocket_handshake_transform=_transform_handshake,
            endpoint=IOT_ENDPOINT,
            client_id=f"#tholz-app_{user_sub}_primary_{int(time.time() * 1000)}",
            clean_session=True,
            keep_alive_secs=30,
            on_connection_interrupted=self._on_interrupted,
        )
        with self._connection_state_lock:
            self._connecting_connection = connection
            self._interrupted_connection_ids.discard(id(connection))
        try:
            connection.connect().result(timeout=self._timeout)
            connection.subscribe(
                topic=self._get_accepted_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )[0].result(timeout=self._timeout)
            connection.subscribe(
                topic=self._update_accepted_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )[0].result(timeout=self._timeout)
            connection.subscribe(
                topic=self._schedule_get_accepted_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )[0].result(timeout=self._timeout)
            connection.subscribe(
                topic=self._schedule_update_accepted_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_message,
            )[0].result(timeout=self._timeout)
            self._activate_connection(connection, expires_at)
        except Exception as exc:
            with self._connection_state_lock:
                if self._connecting_connection is connection:
                    self._connecting_connection = None
                self._interrupted_connection_ids.discard(id(connection))
            with suppress(Exception):
                connection.disconnect().result(timeout=min(self._timeout, 8))
            raise FairlandCloudProtocolError(ERR_MQTT_CONNECT) from exc

    def _request_shadow(self) -> dict[str, Any]:
        self._ensure_connection()
        self._get_response = None
        self._get_client_token = uuid.uuid4().hex
        self._get_event.clear()
        connection = self._connection
        if connection is None:
            raise FairlandCloudProtocolError(ERR_MQTT_CONNECT)
        try:
            connection.publish(
                topic=self._get_topic,
                payload=json.dumps(
                    {"clientToken": self._get_client_token}, separators=(",", ":")
                ),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )[0].result(timeout=self._timeout)
        except Exception as exc:
            self._disconnect()
            raise FairlandCloudProtocolError(ERR_GET_PUBLISH) from exc
        if not self._get_event.wait(self._timeout):
            raise FairlandCloudProtocolError(ERR_GET_TIMEOUT)
        if not isinstance(self._get_response, dict):
            raise FairlandCloudProtocolError(ERR_GET_EMPTY)
        return self._get_response

    def _request_schedule(self) -> dict[str, Any]:
        """Read the verified Fairland heat0Schedule named shadow."""
        self._ensure_connection()
        self._schedule_get_response = None
        self._schedule_get_client_token = uuid.uuid4().hex
        self._schedule_get_event.clear()
        connection = self._connection
        if connection is None:
            raise FairlandCloudProtocolError(ERR_MQTT_CONNECT)
        try:
            connection.publish(
                topic=self._schedule_get_topic,
                payload=json.dumps(
                    {"clientToken": self._schedule_get_client_token},
                    separators=(",", ":"),
                ),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )[0].result(timeout=self._timeout)
        except Exception as exc:
            self._disconnect()
            raise FairlandCloudProtocolError(ERR_GET_PUBLISH) from exc
        if not self._schedule_get_event.wait(self._timeout):
            raise FairlandCloudProtocolError(ERR_GET_TIMEOUT)
        if not isinstance(self._schedule_get_response, dict):
            raise FairlandCloudProtocolError(ERR_GET_EMPTY)
        return self._schedule_get_response

    @staticmethod
    def _reported(document: dict[str, Any]) -> dict[str, Any]:
        reported = document.get("state", {}).get("reported")
        if not isinstance(reported, dict):
            raise FairlandCloudProtocolError(ERR_NO_REPORTED)
        if not isinstance(reported.get("heatings", {}).get("heat0"), dict):
            raise FairlandCloudProtocolError(ERR_NO_HEAT)
        return reported

    def _get_metrics_summary(self) -> dict[str, float]:
        if (
            self._metrics_summary is not None
            and self._metrics_updated_at > time.time() - METRICS_CACHE_SECONDS
        ):
            return self._metrics_summary

        now_ms = int(time.time() * 1000)
        try:
            response = requests.get(
                METRICS_URL,
                headers={
                    "Authorization": self._id_token(),
                    "User-Agent": "TholzHomeAssistant/0.3",
                },
                params={
                    "thingId": self.thing_id,
                    "reset": 0,
                    "beginTime": now_ms - (METRICS_WINDOW_SECONDS * 1000),
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE) from exc
        if response.status_code != HTTP_OK:
            if response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}:
                self._auth.report_authorization_rejected()
                raise FairlandCloudAuthError(ERR_METRICS_RESPONSE)
            raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE)
        raw = response.content
        try:
            if raw.startswith(b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE) from exc
        if not isinstance(document, dict):
            raise FairlandCloudProtocolError(ERR_METRICS_RESPONSE)
        summary = summarize_metrics_document(document, now_ms)
        self._metrics_summary = summary
        self._metrics_updated_at = time.time()
        return summary

    def _with_cached_metrics(self, reported: dict[str, Any]) -> dict[str, Any]:
        result = dict(reported)
        if self._metrics_summary is not None:
            result["metricsSummary"] = self._metrics_summary
        return result

    def get_status(self) -> dict[str, Any]:
        """Read and normalize the classic shadow's reported state."""
        with self._lock:
            reported = dict(self._reported(self._request_shadow()))
            try:
                reported["metricsSummary"] = self._get_metrics_summary()
            except FairlandCloudAuthError:
                raise
            except FairlandCloudError as exc:
                _LOGGER.warning(
                    "Unable to refresh Tholz Fairland cloud metrics: %s", exc
                )
                self._metrics_updated_at = time.time()
                if self._metrics_summary is None:
                    self._metrics_summary = {}
                reported = self._with_cached_metrics(reported)
            try:
                schedule_document = self._request_schedule()
                self._last_schedule = normalize_schedule_document(schedule_document)
                version = schedule_document.get("version")
                timestamp = schedule_document.get("timestamp")
                self._last_schedule_version = (
                    version
                    if isinstance(version, int) and not isinstance(version, bool)
                    else None
                )
                self._last_schedule_timestamp = (
                    timestamp
                    if isinstance(timestamp, int) and not isinstance(timestamp, bool)
                    else None
                )
            except FairlandCloudError as exc:
                _LOGGER.warning("Unable to refresh Tholz heat0Schedule: %s", exc)
            if self._last_schedule is not None:
                reported["schedule"] = self._last_schedule
                reported["scheduleVersion"] = self._last_schedule_version
                reported["scheduleTimestamp"] = self._last_schedule_timestamp
            self._last_reported = reported
            return reported

    @staticmethod
    def _matches(reported: dict[str, Any], changes: dict[str, int]) -> bool:
        heat = reported.get("heatings", {}).get("heat0", {})
        return all(heat.get(key) == value for key, value in changes.items())

    @classmethod
    def _converged(cls, document: dict[str, Any], changes: dict[str, int]) -> bool:
        reported = cls._reported(document)
        no_delta = not document.get("state", {}).get("delta")
        return cls._matches(reported, changes) and no_delta

    def _publish_changes(self, changes: dict[str, int]) -> None:
        self._ensure_connection()
        self._update_response = None
        self._update_client_token = uuid.uuid4().hex
        self._update_event.clear()
        connection = self._connection
        if connection is None:
            raise FairlandCloudProtocolError(ERR_MQTT_CONNECT)
        try:
            connection.publish(
                topic=self._update_topic,
                payload=build_shadow_update(changes, self._update_client_token),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )[0].result(timeout=self._timeout)
        except Exception as exc:
            self._disconnect()
            raise FairlandCloudProtocolError(ERR_UPDATE_PUBLISH) from exc
        if not self._update_event.wait(self._timeout):
            raise FairlandCloudProtocolError(ERR_UPDATE_ACCEPT)

    def _wait_for_convergence(self, changes: dict[str, int]) -> dict[str, Any]:
        deadline = time.monotonic() + self._convergence_timeout
        while time.monotonic() < deadline:
            document = self._request_shadow()
            reported = self._with_cached_metrics(self._reported(document))
            self._last_reported = reported
            if self._converged(document, changes):
                return reported
            time.sleep(0.5)
        raise FairlandCloudProtocolError(ERR_UPDATE_CONVERGENCE)

    def set_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Publish an allowlisted delta and require reported-state convergence."""
        with self._lock:
            if self._last_reported is None:
                self.get_status()
            cached_reported = self._last_reported
            if cached_reported is None:
                raise FairlandCloudProtocolError(ERR_NO_REPORTED)
            changes = extract_mutable_changes(payload, None)
            requested_heat = payload["heatings"]["heat0"]
            if set(requested_heat) - MUTABLE_HEAT_FIELDS:
                # Legacy full snapshots retain delta semantics. Narrow entity
                # payloads express intent; decide their no-op against fresh state.
                changes = extract_mutable_changes(payload, cached_reported)

            fresh_reported = self.get_status()
            current_heat = fresh_reported["heatings"]["heat0"]
            if "sp" in changes:
                try:
                    temperature_policy(
                        current_heat, changes.get("opMode")
                    ).validate_raw(changes["sp"])
                except FairlandTemperatureError as exc:
                    raise FairlandCloudProtocolError(str(exc)) from exc
            changes = {
                key: value
                for key, value in changes.items()
                if current_heat.get(key) != value
            }
            if not changes:
                return fresh_reported
            try:
                baseline = {key: current_heat[key] for key in changes}
            except KeyError as exc:
                raise FairlandCloudProtocolError(ERR_MISSING_BASELINE) from exc
            try:
                extract_mutable_changes({"heatings": {"heat0": baseline}}, None)
                if "sp" in baseline:
                    # Restoration returns to the original mode, not the mode
                    # requested by a combined operation-mode/setpoint command.
                    temperature_policy(current_heat).validate_raw(baseline["sp"])
            except (FairlandCloudProtocolError, FairlandTemperatureError) as exc:
                raise FairlandCloudProtocolError(ERR_UNSAFE_BASELINE) from exc

            self._ensure_connection()
            try:
                self._publish_changes(changes)
                return self._wait_for_convergence(changes)
            except FairlandCloudError:
                # Any failure after publish (protocol, transient or auth) may
                # leave the device at the requested state; always try to restore.
                try:
                    self._publish_changes(baseline)
                    self._wait_for_convergence(baseline)
                except FairlandCloudError as rollback_exc:
                    raise FairlandCloudProtocolError(
                        ERR_ROLLBACK_FAILED
                    ) from rollback_exc
                raise
