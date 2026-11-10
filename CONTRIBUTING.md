# Contributing

Contributions are welcome through issues and pull requests.

## Development

1. Create a branch from `main`.
2. Keep the integration cloud-only and limited to Fairland/TurboSilence heat pumps.
3. Add or update tests for behavior changes.
4. Run the stack-specific offline checks below, Ruff lint and formatting.
5. Update both user-language guides and the changelog when behavior changes.

### Reproduce the checks

Install each stack into its own disposable environment, never the production Home Assistant environment:

| Home Assistant Core | Python | Requirements | Pytest framework | Extra pytest arguments |
| --- | --- | --- | --- | --- |
| 2025.2.4 | 3.13 | `requirements.txt` | `pytest-homeassistant-custom-component==0.13.214` | None |
| 2026.10.1 | 3.14 | `requirements-ha2026.txt` | `pytest-homeassistant-custom-component==0.13.372` | None |
| 2026.11.1 | 3.14.9 | `requirements-ha202611.txt` | `pytest-homeassistant==2026.11.1` (official) | `-p no:respx` |

Do not install both frameworks in one environment. Keep the official framework and Core on the same release; its requirements use the matching official Core constraints. The setup-flow test imports the official helper when that module exists and uses the legacy helper only when the official module is absent. Import or dependency errors in an installed official framework must fail rather than silently falling back.

Example for the official stack, run from the repository root:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3.14 -m venv /path/outside/repository/ha202611-venv
PYTHON=/path/outside/repository/ha202611-venv/bin/python
"$PYTHON" -m pip install -r requirements-ha202611.txt
"$PYTHON" -m pip check
"$PYTHON" -B -m pytest -p no:respx -p no:cacheprovider --disable-socket --allow-unix-socket
"$PYTHON" -m ruff check --no-cache .
"$PYTHON" -m ruff format --check --no-cache .
"$PYTHON" scripts/build_release.py --out-dir /path/outside/repository/empty-directory
```

Verify that the selected interpreter is Python 3.14.9 for this example. For a legacy stack, select its Python and requirements file from the table and omit `-p no:respx`. That option disables only the separate respx pytest plugin, allowing Home Assistant to install its HTTP aliases first; the respx library and Home Assistant HTTP mocks remain available. CI applies the option only to the official stack. The shared pytest configuration sets the repository import path and limits discovery to `tests`.

The test suite exercises actual Home Assistant setup, entities, unload/reload and reauthentication with mocked cloud boundaries. Network connections are disabled; setup tests also reject any attempted equipment command. Passing this suite is not a live-account or physical-equipment certification.

The local builder creates deterministic integration and source ZIPs plus `SHA256SUMS.txt`. It does not create Git tags, GitHub releases, uploads, or visibility changes. Output must be outside the checkout and absent or empty. Treat release publication as a separate maintainer decision.

HACS online validation requires a public repository and is deliberately skipped while the repository is private. This skip is not a validation pass. Repository structure, manifest, bundled brand assets, packaging and Home Assistant tests can be checked privately.

Never commit real credentials, account or device identifiers, Home Assistant storage, logs containing private data, or vendor application artifacts. Tests must use synthetic values and must not contact or control real equipment.

Credential changes must follow the Home Assistant config-entry and reauthentication lifecycle. Validate new credentials before persisting them, mask password inputs, keep options free of credentials, and do not add custom encryption that requires an embedded or colocated decryption key.

Report suspected vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not through a public issue.

By contributing, you agree that your changes are licensed under the MIT License.
