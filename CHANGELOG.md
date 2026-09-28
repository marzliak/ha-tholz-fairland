# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.3.0] - 2026-09-28

### Added

- Normal account onboarding with e-mail or username and password.
- Read-only discovery of compatible Fairland/TurboSilence heat pumps linked to the account.
- Device selection when an account contains multiple compatible heat pumps.
- Cognito verification-code and new-password challenge handling during setup, reauthentication, and credential updates.
- Automatic session recovery through Cognito SRP when all stored refresh tokens are rejected.

### Changed

- Credentials are validated with a read-only device request before they are persisted.
- Automatic recovery is the only recovery policy; existing entries migrate to schema 1.4.
- Reconfigure now validates replacement credentials before updating and reloading the entry.
- The development dependency set records reviewed versions while the integration defers shared `boto3` and `pycognito` resolution to Home Assistant to avoid runtime conflicts.

### Security

- Passwords, tokens, authorization material, Cognito challenge sessions, and SRP values remain excluded from integration logs and diagnostics.
- Documentation now states explicitly that Home Assistant config-entry storage and backups contain reusable credentials and must be protected.

## [0.2.2] - 2026-09-28

### Added

- Cognito SRP recovery, atomic refresh-token rotation, bounded retries, single-flight recovery, and Home Assistant reauthentication.
- AWS IoT custom-authorizer transport used by the current vendor application.

### Fixed

- Authentication rejection handling, stale connection reconstruction, lifecycle races, and healthy device error code `0` handling.
