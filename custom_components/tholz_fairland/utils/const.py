"""Constants for the Tholz Fairland integration."""

from datetime import timedelta

DOMAIN = "tholz_fairland"
CONFIG_ENTRY_VERSION = 1

CONF_NAME_KEY = "name"
CONF_POLLING_INTERVAL_KEY = "polling_interval"
CONF_POLLING_INTERVAL_DEFAULT_VALUE = 30
CONF_COGNITO_USER_SUB_KEY = "cognito_user_sub"
CONF_COGNITO_CLIENT_SECRET_KEY = "cognito_client_secret"  # noqa: S105
CONF_COGNITO_REFRESH_TOKEN_KEY = "cognito_refresh_token"  # noqa: S105
CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY = "credential_seed_refresh_token"  # noqa: S105
CONF_THING_ID_KEY = "thing_id"

ENTITIES_SCAN_INTERVAL = timedelta(seconds=1)
