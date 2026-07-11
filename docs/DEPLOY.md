# Deploying the saturn viewer on dr.eamer.dev

The public viewer's default model workflow requires both optional extras (`pip install 'saturn-dissect[web,llm]'`). Server deployment is wired through `sm` and Caddy. A viewer without provider credentials remains usable: model insight fails open and the deterministic analysis is still returned.

## Local dev

```bash
pip install -e '.[web,llm]'
export ANTHROPIC_API_KEY='...'
saturn serve --dir ./findings --port 5043
open http://127.0.0.1:5043
```

## Server deployment (dr.eamer.dev)

### 1. `sm` registration (already live)

`~/service_manager.py` has a `saturn-viewer` entry at port 5043:

```python
'saturn-viewer': {
    'name': 'Saturn Viewer',
    'script': '/home/coolhand/projects/saturn/saturn/scripts/start.sh',
    'working_dir': '/home/coolhand/projects/saturn/saturn',
    'port': 5043,
    'health_endpoint': 'http://localhost:5043/health',
    'start_timeout': 15,
    'description': 'Live saturn findings workbench (WCAG 2.2 AA, reads and creates findings in /home/coolhand/saturn-findings)'
}
```

Manage with the usual verbs:

```bash
sm status                 # confirm Running
sm restart saturn-viewer
sm logs saturn-viewer
sm stop saturn-viewer
```

### 2. `scripts/start.sh` (already committed)

Install the deployed environment with the viewer and provider gateway:

```bash
pip install -e '.[web,llm]'
```

Configure the service manager to inject `ANTHROPIC_API_KEY` into the process environment for the default `anthropic:claude-opus-4-7` workflow. Store the secret in the deployment platform's secret/environment facility, outside this repository; do not put it in `scripts/start.sh`, a tracked dotenv file, or service-manager source. A different `SATURN_DEFAULT_LLM` requires that provider's standard environment key.

If the key is missing or the provider call fails, Saturn records the model-stage error and continues with deterministic findings. This fail-open behavior keeps analysis available but means the public model narrative is absent until the service environment is corrected.

`scripts/start.sh` activates the saturn venv and runs gunicorn against the Flask app factory:

```bash
exec gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
```

Environment knobs: `ANTHROPIC_API_KEY`, `SATURN_DEFAULT_LLM`, `SATURN_FINDINGS_DIR`, `SATURN_PORT`, `SATURN_HOST`, `SATURN_WORKERS`, `SATURN_THREADS`. Defaults: `anthropic:claude-opus-4-7`, `/home/coolhand/saturn-findings`, `5043`, `127.0.0.1`, `1`, `8`. `ANTHROPIC_API_KEY` has no default and must be explicitly injected for the default public model workflow. Keep one worker while background job state is process-local; the eight request threads serve reads and job polling while work runs in the bounded executor.

### 3. Findings directory

`/home/coolhand/saturn-findings/` is the source for current readings. Drop any `*.json` file saturn produces (profile or compare) into it and the index picks it up on the next request. The matching `*.html` file can live alongside the JSON but the viewer doesn't need it.

The deployment also sets `SATURN_LEGACY_ARCHIVE_DIR` to the preserved historical
artifact directory. Only complete, top-level `<safe-id>.html` and `<safe-id>.ipynb`
pairs are indexed. Historical HTML is returned with a sandbox content security
policy; notebooks download as attachments. Files with other extensions, nested
paths and traversal attempts are never served. Dot-prefixed historical IDs are
allowed only when they match the same strict character allowlist and complete pair.

Data flow:

```
saturn analyze <source> \
    --out     ~/saturn-findings/<name>.html \
    --findings ~/saturn-findings/<name>.json
```

Then refresh the viewer in the browser. No restart needed. The index sorts newest-first by mtime.

### 4. Caddy route (needs `@geepers_caddy`)

Append to `/etc/caddy/Caddyfile` inside the `dr.eamer.dev { ... }` block. Two directives: a redirect for bare `/saturn` and the path-stripped reverse proxy for everything under it:

```caddyfile
redir /saturn /saturn/ 308

handle_path /saturn/* {
    reverse_proxy localhost:5043 {
        header_up X-Forwarded-Prefix /saturn
    }
}
```

The `redir` is load-bearing: `handle_path /saturn/*` does not match the bare `/saturn` URL, which otherwise falls through to whatever static file_server is behind it. The `header_up` is also load-bearing: without it, the viewer's HTML links drop the `/saturn/` prefix and follow-ups 404. The app honors the header only when `SATURN_TRUST_FORWARDED_PREFIX=1` is set (scripts/start.sh sets it on the sm-managed deployment), so a direct-to-gunicorn caller cannot spoof a prefix.

Apply via `@geepers_caddy` (sole authority; do not hand-edit). Then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -s https://dr.eamer.dev/saturn/health
```

Expected: `{"findings_dir":"/home/coolhand/saturn-findings","status":"ok"}`.

## Security posture

- **Mutation surface.** `POST /analyze` uploads a local dataset, `POST /analyze-hf` queues a Hugging Face dataset, and `POST /backfill/<id>` adds an LLM reading to an existing finding. Bound request sizes, extension checks, the bounded job queue, and deployment access controls are therefore security boundaries. GET routes continue to serve findings and job status.
- **Path-traversal guard.** `_safe_findings_path` in `saturn/viewer/app.py` resolves every `<id>` against the configured findings dir and 404s anything that escapes. Belt and suspenders; Flask's default string converter already forbids `/`.
- **No PII by design.** saturn's findings are aggregates (counts, rates, alerts, top values, language mix). Still: treat the findings dir like any public directory. Don't drop findings from a dataset you can't share. `top_values` on a free-text column can surface snippets of actual content.
- **Rate limiting.** Not configured in-app. If traffic ever matters, add Caddy's `rate_limit` plugin at the path.
- **Concurrent writes.** `saturn analyze` writes non-atomically; viewer can land on a half-written file. The loader's JSON decode catches this and the index hides the row until the next request. No corrupted state.

## Verification

```bash
sm status | grep saturn                         # expect Running + healthy
curl -s http://127.0.0.1:5043/health            # expect {"status":"ok",...}
curl -s http://127.0.0.1:5043/ | grep -c Find   # expect 1+ (Findings heading)
# after Caddy applied:
curl -s https://dr.eamer.dev/saturn/health
curl -sI https://dr.eamer.dev/saturn/ | head -1  # expect HTTP/2 200
```
