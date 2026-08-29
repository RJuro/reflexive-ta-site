# Deploying MASSHINE (Coolify)

One container: FastAPI backend + the v4 static frontend, built from the root `Dockerfile`.
No external services (no Postgres/Redis) — state is per-project SQLite files under one data
directory.

## New Coolify project

1. **New Resource → Application → Docker (from a Git repo).**
2. Point it at `RJuro/reflexive-ta-site`, branch `main`. Coolify will detect the root
   `Dockerfile` and build from it — the build context must be the **repo root**, since
   `web/` and `packs/` sit alongside `engine/` and the app locates them by that relative
   layout (`masshine/config.py`, `masshine/packs.py`).
3. **Port:** the container listens on `8760` (`EXPOSE 8760`; the entrypoint also respects
   `$PORT` if Coolify injects a different one).

## Environment variables (Coolify → this app → Environment Variables)

| Variable | Required | Notes |
|---|---|---|
| `MASSHINE_BASE_URL` | yes | MiniMax-compatible OpenAI API base URL |
| `MASSHINE_API_KEY` | yes | MiniMax API key — set only here, never commit it |
| `MASSHINE_MODEL` | no | defaults to `MiniMax-M3` |
| `MASSHINE_PIN` | recommended | gates the whole site behind a styled in-app PIN screen (session cookie; HTTP Basic with this string as the password also still works for curl/scripts). Unset = no auth at all — fine for a private network, not for a public link. |
| `MASSHINE_VIEW_PIN` | optional | a second, read-only PIN for coauthors: anyone using it can browse everything but gets 403 on any change — no runs, no notes, no deletes. Editors use `MASSHINE_PIN` as before. |
| `MASSHINE_DATA_DIR` | already set | baked into the image as `/data` — leave it unless you also move the volume below |
| `MASSHINE_RETRIES` | no | extra whole-call retries on a mid-stream idle death; default 0 |
| `MASSHINE_LLM_LOG` | no | set to `1` to append a per-call JSONL ledger to `exports/` (not persisted unless that path is also volume-mounted — skip for now) |

### Alternative provider: Mistral (EU/GDPR)

The LLM client's default profile (above) targets the MiniMax production endpoint. For a
deployment that needs an EU/GDPR-compliant provider under contract, set `MASSHINE_PROVIDER=mistral`
to switch the same OpenAI-compatible client to `api.mistral.ai` instead — no other engine
behavior changes (same streaming, same usage/cache-token ledger).

| Variable | Required | Notes |
|---|---|---|
| `MASSHINE_PROVIDER` | to enable | set to `mistral`; unset/empty keeps the default MiniMax profile above |
| `MISTRAL_API_KEY` | yes (mistral) | Mistral API key (fallback: `MASSHINE_MISTRAL_API_KEY`) |
| `MASSHINE_MISTRAL_BASE_URL` | no | defaults to `https://api.mistral.ai/v1` |
| `MASSHINE_MISTRAL_MODEL` | no | defaults to `glm-5-2` |

## Selecting a model (P10.1c)

A researcher can pick which model runs a project's coding/theming/reading jobs, on top of the
provider config above:

- **Server default** — with nothing configured per-project, every job runs under whichever
  provider profile is set above (MiniMax, or Mistral via `MASSHINE_PROVIDER=mistral`). Nothing
  in this section is required for that — it's the existing behavior, untouched.
- **Per-project default** — `PATCH /projects/{pid}` with `{"model_id": "glm-5-2"}` sets that
  project's default model (any id from `GET /models`); `{"model_id": null}` clears it back to
  the server default. `GET /projects/{pid}` echoes the project's current `model_id`.
- **Per-run override** — `/code`, `/read`, `/themes`, `/recode`, and `/synthesize` all accept an
  optional `model_id` in their POST body; it wins for that one run only, without touching the
  project's default.
- **The registry** — `GET /models` lists the selectable models (`id`, `label`, `provider`,
  `model`, `note`, `available` — whether that provider's credentials are actually configured
  here) plus `default_model_id`. To replace the built-in list entirely, set `MASSHINE_MODELS` to
  a JSON array, e.g.:

  ```json
  [{"id": "glm-5-2", "label": "GLM-5.2 (Mistral, EU)", "provider": "mistral", "model": "glm-5-2",
    "note": "GDPR — university contract"}]
  ```

  Each entry needs at least `id`, `provider` (`minimax` | `mistral`), and `model`; a malformed
  entry is dropped, and invalid/absent JSON falls back to the built-in list — this can never
  crash the app.

Every provider credential above (`MASSHINE_API_KEY`, `MISTRAL_API_KEY`/`MASSHINE_MISTRAL_API_KEY`)
still has to be set for a model on that provider to actually run — `available: false` in
`GET /models` means the id is listed but currently unusable.

## Persistent storage — do this before the first real coding run

Add a **Storage / Volume** in Coolify mounted at `/data` inside the container. Without it,
every redeploy wipes all projects: SQLite state lives at `/data/registry.db` and
`/data/projects/<id>/masshine.db`.

## Health check

`GET /health` returns `{"ok": true}` and is intentionally exempt from the PIN gate — point
Coolify's health check at that path. The image also carries a self-contained Docker
`HEALTHCHECK` hitting the same endpoint.

## Demo project (auto-seeded)

On first boot, if the mounted `/data` volume has zero projects, the app seeds one
automatically from `engine/seed_data/` — "Migration panel (demo)", the same 2-interview,
3-lens panel run shown in local dev (421 codes, 7 themes), reconstructed with **zero LLM
calls** from a cached run. This only happens once: any later restart or redeploy sees an
existing project and skips it, so it never overwrites real work. To deploy without the
demo (e.g. a fresh instance for real research), set `MASSHINE_SEED_DEMO=0`.

## After first deploy

Open the app URL, enter the PIN when the browser's Basic-auth prompt appears (if
`MASSHINE_PIN` is set) — the demo project should already be there to explore. To add real
data: create a project, add a source (`.txt`/`.md`), and run coding. Coding/theming take
several minutes and call the paid MiniMax API on every run — anyone with the PIN can
trigger one.
