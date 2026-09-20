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

5. Update user documentation when behavior changes.

Never commit real credentials, account or device identifiers, Home Assistant storage, logs containing private data, or vendor application artifacts. Tests must use synthetic values and must not contact or control real equipment.

By contributing, you agree that your changes are licensed under the MIT License.
