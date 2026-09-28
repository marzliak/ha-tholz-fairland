# Security policy

## Supported versions

Security fixes are provided for the latest tagged release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or for any report containing credentials, tokens, account identifiers, device identifiers, Home Assistant storage, backups, or unsanitized logs.

Use GitHub's private vulnerability reporting for this repository. Include:

- the affected integration version and Home Assistant version;
- a concise reproduction using synthetic data where possible;
- the security impact;
- whether the vendor cloud or physical equipment was contacted;
- sanitized logs or a minimal proof of concept.

Never include real passwords, refresh/access/ID tokens, Cognito challenge sessions, SRP values, authorization headers, MQTT signatures, cookies, complete config entries, `.storage` files, or Home Assistant backups.

## Credential model

The integration follows Home Assistant's normal config-entry model. It stores the account username, password, refresh tokens, cloud user identifier, and device identifier so it can recover sessions without user intervention. Password fields are masked in the UI but are not individually encrypted in config-entry storage.

Protect the Home Assistant host and encrypt and restrict backups. If either is exposed, remove the integration, rotate the cloud password, and invalidate affected sessions where the vendor permits it.
