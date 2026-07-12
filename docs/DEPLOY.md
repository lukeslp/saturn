# Deploying the saturn viewer on dr.eamer.dev

The public viewer's default model workflow requires both optional extras (`pip install 'saturn-dissect[web,llm]'`). A viewer without provider credentials remains usable: model insight fails open and the deterministic analysis is still returned.

## Local dev

```bash
pip install -e '.[web,llm]'
export OPENAI_API_KEY='...'
saturn serve --dir ./findings --port 5043
open http://127.0.0.1:5043
```

## Server deployment (dr.eamer.dev)

### 1. Service registration

Run the viewer as the unprivileged `coolhand` user on loopback port 5043. Install `deploy/saturn-viewer.service`; its stable paths are:

```text
working directory: /home/coolhand/projects/saturn
command: /home/coolhand/projects/saturn/current/scripts/start.sh
health check: http://localhost:5043/health
```

### 2. `scripts/start.sh` (already committed)

The launcher resolves the physical commit-addressed source directory behind `current`, verifies its provenance manifest, and runs Gunicorn with the matching environment's Python executable under `venvs/<commit>/`. Source and environment directories are never modified after activation. `PYTHONDONTWRITEBYTECODE=1` prevents runtime cache files from appearing in the verified source tree.

Configure the service to inject `OPENAI_API_KEY` into the process environment for the default `openai:gpt-5.6-luna` workflow. Store the secret in the deployment platform's secret/environment facility, outside this repository; do not put it in `scripts/start.sh` or a tracked dotenv file. A different `SATURN_DEFAULT_LLM` requires that provider's standard environment key.

If the key is missing or the provider call fails, Saturn records the model-stage error and continues with deterministic findings. This fail-open behavior keeps analysis available but means the public model narrative is absent until the service environment is corrected.

`scripts/start.sh` activates the saturn venv and runs gunicorn against the Flask app factory:

```bash
exec "$VENV_DIR/bin/python" -m gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
```

Environment knobs: `OPENAI_API_KEY`, `SATURN_DEFAULT_LLM`, `SATURN_FINDINGS_DIR`, `SATURN_PORT`, `SATURN_HOST`, `SATURN_WORKERS`, `SATURN_THREADS`, and `SATURN_DEPLOY_ROOT`. Defaults: `openai:gpt-5.6-luna`, `/home/coolhand/saturn-findings`, `5043`, `127.0.0.1`, `1`, `8`, and the parent of `releases/`. The launcher sets `SATURN_DEPLOY_MANIFEST` to the exact active release manifest. `OPENAI_API_KEY` has no default and must be explicitly injected for the default public model workflow. Keep one worker while background job state is process-local; the eight request threads serve reads and job polling while work runs in the bounded executor.

### 3. Deploy one reviewed commit

Deploy from an explicit commit, not from a mutable working tree. The release script creates fresh source and environment staging directories, verifies an exact tracked-file inventory, installs the environment outside the source tree, then atomically switches `current`:

```bash
git fetch origin
COMMIT="$(git rev-parse origin/main)"
scripts/deploy-release.sh "$COMMIT" /home/coolhand/projects/saturn "$PWD"
sudo systemctl restart saturn-viewer
```

The record command refuses reused staging, missing or changed tracked files, extra regular files (including `sitecustomize.py`), and every symlink. The `/health` response exposes only service status, the default model, and `deployment_commit`; compare the commit with the release SHA after restart.

Rollback does not rebuild or mutate either release. Atomically point `current` at a previously verified release and restart:

```bash
scripts/activate-release.sh PREVIOUS_FULL_COMMIT /home/coolhand/projects/saturn
sudo systemctl restart saturn-viewer
```

### 4. Findings directory

`/home/coolhand/saturn-findings/` is the source for current readings. Drop any `*.json` file saturn produces (profile or compare) into it and the index picks it up on the next request. The matching `*.html` file can live alongside the JSON but the viewer doesn't need it.

The deployment also sets `SATURN_LEGACY_ARCHIVE_DIR` to the preserved historical
artifact directory. Only complete, top-level `<safe-id>.html` and `<safe-id>.ipynb`
pairs are indexed or served. Both paths are canonicalized and must remain regular
files inside the archive root, which rejects broken links and symlink escapes.
Historical HTML is returned under an offline-only sandbox content security policy;
notebooks download as attachments. Files with other extensions, nested paths and
traversal attempts are never served. Dot-prefixed historical IDs are allowed only
when they match the same strict character allowlist and complete pair.

Data flow:

```
saturn analyze <source> \
    --out     ~/saturn-findings/<name>.html \
    --findings ~/saturn-findings/<name>.json
```

Then refresh the viewer in the browser. No restart needed. The index sorts newest-first by mtime.

### 5. Caddy route

Append to `/etc/caddy/Caddyfile` inside the `dr.eamer.dev { ... }` block. Two directives: a redirect for bare `/saturn` and the path-stripped reverse proxy for everything under it:

```caddyfile
redir /saturn /saturn/ 308

handle_path /saturn/* {
    reverse_proxy localhost:5043 {
        header_up X-Forwarded-Prefix /saturn
    }
}
```

The `redir` is load-bearing: `handle_path /saturn/*` does not match the bare `/saturn` URL, which otherwise falls through to whatever static file_server is behind it. The `header_up` is also load-bearing: without it, the viewer's HTML links drop the `/saturn/` prefix and follow-ups 404. The app honors the header only when `SATURN_TRUST_FORWARDED_PREFIX=1` is set (`scripts/start.sh` sets it for the managed service), so a direct-to-gunicorn caller cannot spoof a prefix.

Back up the active configuration, validate the candidate configuration, and reload only after validation succeeds. Then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -s https://dr.eamer.dev/saturn/health
```

Expected: a JSON object containing `"status":"ok"` and the active `deployment_commit`. Filesystem paths are intentionally omitted.

## Security posture

- **Mutation surface.** `POST /analyze` uploads a local dataset, `POST /analyze-hf` queues a Hugging Face dataset, and `POST /backfill/<id>` adds an LLM reading to an existing finding. Bound request sizes, extension checks, the bounded job queue, and deployment access controls are therefore security boundaries. GET routes continue to serve findings and job status.
- **Path-traversal guard.** `_safe_findings_path` in `saturn/viewer/app.py` resolves every `<id>` against the configured findings dir and 404s anything that escapes. Belt and suspenders; Flask's default string converter already forbids `/`.
- **No PII by design.** saturn's findings are aggregates (counts, rates, alerts, top values, language mix). Still: treat the findings dir like any public directory. Don't drop findings from a dataset you can't share. `top_values` on a free-text column can surface snippets of actual content.
- **Rate limiting.** Not configured in-app. If traffic ever matters, add Caddy's `rate_limit` plugin at the path.
- **Concurrent writes.** `saturn analyze` writes non-atomically; viewer can land on a half-written file. The loader's JSON decode catches this and the index hides the row until the next request. No corrupted state.

## Verification

```bash
systemctl is-active saturn-viewer               # expect active
curl -s http://127.0.0.1:5043/health            # expect {"status":"ok",...}
curl -s http://127.0.0.1:5043/ | grep -c Find   # expect 1+ (Findings heading)
# after Caddy applied:
curl -s https://dr.eamer.dev/saturn/health
curl -sI https://dr.eamer.dev/saturn/ | head -1  # expect HTTP/2 200
```
