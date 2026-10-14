# Contributing

Contributions are welcome through issues and pull requests.

## Development

1. Create a branch from `main`.
2. Keep the integration cloud-only and limited to Fairland/TurboSilence heat pumps.
3. Add or update tests for behavior changes.
4. Run:

   ```bash
   python3 -m pytest -p no:cacheprovider
   python3 -m ruff check .
   python3 -m ruff format --check .
   ```

5. Update both user-language guides and the changelog when behavior changes.

### Reproduce the checks

Use Python 3.13 with `requirements.txt` for Home Assistant 2025.2.4, or Python 3.14 with `requirements-ha2026.txt` for Home Assistant 2026.10.1. Install into a disposable environment, never the production Home Assistant environment.

```bash
python3 -m pytest -p no:cacheprovider --disable-socket --allow-unix-socket
python3 -m ruff check .
python3 -m ruff format --check .
python3 scripts/build_release.py --out-dir /path/outside/repository/empty-directory
```

The test suite exercises actual Home Assistant setup, entities, unload/reload and reauthentication with mocked cloud boundaries. Network connections are disabled; setup tests also reject any attempted equipment command. Passing this suite is not a live-account or physical-equipment certification.

The local builder creates deterministic integration and source ZIPs plus `SHA256SUMS.txt`. It does not create Git tags, GitHub releases, uploads, or visibility changes. Output must be outside the checkout and absent or empty. Treat release publication as a separate maintainer decision.

HACS online validation requires a public repository and is deliberately skipped while the repository is private. This skip is not a validation pass. Repository structure, manifest, bundled brand assets, packaging and Home Assistant tests can be checked privately.

Never commit real credentials, account or device identifiers, Home Assistant storage, logs containing private data, or vendor application artifacts. Tests must use synthetic values and must not contact or control real equipment.

Credential changes must follow the Home Assistant config-entry and reauthentication lifecycle. Validate new credentials before persisting them, mask password inputs, keep options free of credentials, and do not add custom encryption that requires an embedded or colocated decryption key.

Report suspected vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not through a public issue.

By contributing, you agree that your changes are licensed under the MIT License.
