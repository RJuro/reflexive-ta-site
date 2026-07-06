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
| `MASSHINE_PIN` | recommended | gates the whole site behind HTTP Basic auth (any username, this string as the password). Unset = no auth at all — fine for a private network, not for a public link. |
| `MASSHINE_DATA_DIR` | already set | baked into the image as `/data` — leave it unless you also move the volume below |
| `MASSHINE_RETRIES` | no | extra whole-call retries on a mid-stream idle death; default 0 |
| `MASSHINE_LLM_LOG` | no | set to `1` to append a per-call JSONL ledger to `exports/` (not persisted unless that path is also volume-mounted — skip for now) |

## Persistent storage — do this before the first real coding run

Add a **Storage / Volume** in Coolify mounted at `/data` inside the container. Without it,
every redeploy wipes all projects: SQLite state lives at `/data/registry.db` and
`/data/projects/<id>/masshine.db`.

## Health check

`GET /health` returns `{"ok": true}` and is intentionally exempt from the PIN gate — point
Coolify's health check at that path. The image also carries a self-contained Docker
`HEALTHCHECK` hitting the same endpoint.

## After first deploy

Open the app URL, enter the PIN when the browser's Basic-auth prompt appears (if
`MASSHINE_PIN` is set), create a project, add a source (`.txt`/`.md`), and run coding.
Coding/theming take several minutes and call the paid MiniMax API on every run — anyone
with the PIN can trigger one.
