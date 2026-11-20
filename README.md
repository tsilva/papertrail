<p align="center">
  <img src="./logo.png" alt="papertrail" width="512" />
  <br />
  <!-- repo-tagline:start -->
  <strong>🧾 Sort documents into sidecar-backed order 🧾</strong>
  <!-- repo-tagline:end -->
</p>

Papertrail organizes documents into a processed collection with JSON sidecars.
It classifies PDFs and other supported files, derives filenames from the
recorded metadata, and can verify hashes or repair names.

## Quickstart

Use Python 3.12+, uv, and a configured local AgentBridge with Codex access.
Follow the [profile setup](docs/papertrail-guide.md#install), then run:

```bash
papertrail --profile personal pipeline
```

Check the processed directory for documents and their sidecars. Put live
documents and profiles outside the repository.

[Papertrail setup and operator reference](docs/papertrail-guide.md).

## License

[MIT License](LICENSE).
