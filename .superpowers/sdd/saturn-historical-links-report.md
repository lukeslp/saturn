# Saturn historical links verification

Date: 2026-07-11 UTC

## Scope and root cause

The preserved static Saturn index referenced 233 unique `/saturn/view/<id>`
readings. Each reading still had an HTML report and notebook under
`/home/coolhand/www/dr.eamer.dev/saturn/view`, but Caddy now sends the entire
`/saturn/*` prefix to the Flask viewer. The viewer previously required a current
JSON finding in `/home/coolhand/saturn-findings`, so every historical link
returned 404.

Historical artifacts remain outside the repository. The viewer indexes and
serves only a complete `<id>.html` plus `<id>.ipynb` pair after both paths are
canonicalized, confirmed as regular files, and confirmed to remain inside the
configured archive root. Unsupported IDs, incomplete pairs, broken links,
symlink escapes, nested paths, and traversal attempts return 404.

## Privacy-safe archive inventory

The inventory script scanned only top-level `*.html` names. It validated the ID
against Saturn's strict character allowlist, canonicalized the HTML and matching
notebook paths, rejected paths outside the archive root, and required two regular
files. It emitted no IDs or artifact content. Each private manifest row contained:

```text
sha256(id) | HTML byte count | sha256(HTML) | notebook byte count | sha256(notebook)
```

The sorted rows were joined with LF terminators and hashed again. Summary:

```text
complete_pairs 233
manifest_sha256 1b11c3e5237f601879c9fe6bb35f652fe79b010415e0e8ab03721e01166fc150
html_bytes 53933716
notebook_bytes 7982724
```

No report, notebook, filename, ID, or generated manifest was copied into Git.

## Automated verification

Local command:

```sh
.venv/bin/pytest -q
```

Exact result after the complete-pair, canonical-path, and offline CSP fixes:

```text
........................................................................ [ 17%]
........................................................................ [ 34%]
......................................s................................. [ 52%]
........................................................................ [ 69%]
........................................................................ [ 86%]
.......................................................                  [100%]
414 passed, 1 skipped in 13.50s
```

The seven archive-specific tests cover a valid pair, truthful archive landing
page, optional archive configuration, unsupported/traversal requests, HTML-only
and notebook-only orphans across index/bare/direct routes, and escaped/broken
symlink pairs across index/bare/direct routes.

## Deployment and rollback

Initial deployment backup:

```text
/home/coolhand/backups/saturn-historical-20260711T221545Z
```

The deployment stages changed files below `/home/coolhand/staging`, copies the
current application (excluding its virtual environment and test cache) to a
timestamped directory below `/home/coolhand/backups`, copies only the reviewed
paths into `/home/coolhand/projects/saturn/saturn`, then runs:

```sh
sudo systemctl restart saturn-viewer.service
curl -fsS http://127.0.0.1:5043/health
sudo systemctl is-active saturn-viewer.service
```

Initial status was `active`; the local health endpoint and public
`https://dr.eamer.dev/saturn/health` both returned HTTP 200. Caddy did not change,
so no Caddy reload was performed. Rollback is an `rsync -a` from the timestamped
backup followed by the same service restart and health check.

## Live link crawl

The crawl parsed internal `href`, stylesheet, and script URLs from the public
root, then added bare landing, `.html`, and `.ipynb` URLs for every validated
pair. It sent bounded concurrent HEAD requests and counted any status 400 or
higher as broken. Initial deployment result:

```text
archive_ids=233 root_internal_links=238 checked=704 broken=0
html_csp=sandbox allow-scripts
notebook_disposition=attachment; filename=data-trove-shipwrecks.ipynb
```

Negative checks returned 404 for a missing ID, encoded traversal, and an
unsupported extension. The follow-up deployment and crawl below supersede the
initial HTML policy with the offline-only policy.

## Follow-up deployment

The complete-pair and offline CSP fix was staged at:

```text
/home/coolhand/staging/saturn-historical-20260711T222150Z/full
```

The seven focused tests passed on drummer before deployment. The previous live
application was backed up to:

```text
/home/coolhand/backups/saturn-historical-20260711T222150Z
```

After copying the four changed deployment paths, the deployment ran
`sudo systemctl restart saturn-viewer.service`, polled the local health endpoint,
and checked `systemctl is-active`. Final status was `active`; local and public
health returned HTTP 200.

The same public crawl was then repeated:

```text
archive_ids=233 root_internal_links=238 checked=704 broken=0
html_csp=sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'
notebook_disposition=attachment; filename=data-trove-shipwrecks.ipynb
landing_truthful=True
report_text_preserved=True
```

The report check confirms that preserved semantic HTML and inline styling still
arrive. The policy intentionally blocks the legacy Plotly CDN and every other
network channel, so historical charts that depended on that CDN degrade without
network access while report text remains readable. Missing, encoded traversal,
and unsupported-extension checks each returned 404.
