"""Constants for the Tholz Fairland integration."""

from datetime import timedelta

DOMAIN = "tholz_fairland"
# Schema history (major version stays 1 so an older build can still load the
# entry; the additive changes are a MINOR bump):
#   1.1: name, thing_id, cognito_user_sub, cognito_client_secret,
#        cognito_refresh_token, credential_seed_refresh_token, polling_interval.
#   1.2: adds cognito_username, cognito_recovery_mode and (automatic mode only)
#        cognito_password. The recovery seed keeps its legacy key so a rollback
#        build keeps operating. Bumping the MAJOR version would make Home
#        Assistant refuse the entry under rollback (unrecoverable migration_error).
#   1.3: switches to the app's public Cognito client introduced in Tholz 4.1.0;
#        the legacy client-secret key is retained as an empty string so rollback
#        builds can still parse the entry without retaining an obsolete secret.
CONFIG_ENTRY_VERSION = 1
CONFIG_ENTRY_MINOR_VERSION = 3

CONF_NAME_KEY = "name"
CONF_POLLING_INTERVAL_KEY = "polling_interval"
CONF_POLLING_INTERVAL_DEFAULT_VALUE = 30
CONF_COGNITO_USER_SUB_KEY = "cognito_user_sub"
CONF_COGNITO_CLIENT_SECRET_KEY = "cognito_client_secret"  # noqa: S105
CONF_COGNITO_REFRESH_TOKEN_KEY = "cognito_refresh_token"  # noqa: S105
# The recovery seed refresh token. This is the single source of truth for the
# seed; its key is unchanged from schema 1.1 so a rollback build keeps using it.
CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY = "credential_seed_refresh_token"  # noqa: S105
CONF_THING_ID_KEY = "thing_id"

# --- schema 1.2: SRP automatic recovery ----------------------------------
# The human login identity used for USER_SRP_AUTH. Distinct from the immutable
# cognito_user_sub, which the refresh grant's SECRET_HASH is computed against;
# neither may ever be inferred from the other.
CONF_COGNITO_USERNAME_KEY = "cognito_username"
# Only present in automatic mode. Stored in the config entry (and therefore any
# backup that includes it) so recovery can run unattended.
CONF_COGNITO_PASSWORD_KEY = "cognito_password"  # noqa: S105
CONF_RECOVERY_MODE_KEY = "cognito_recovery_mode"

RECOVERY_MODE_AUTOMATIC = "automatic"
RECOVERY_MODE_INTERACTIVE = "interactive"
RECOVERY_MODES = (RECOVERY_MODE_AUTOMATIC, RECOVERY_MODE_INTERACTIVE)

ENTITIES_SCAN_INTERVAL = timedelta(seconds=1)
