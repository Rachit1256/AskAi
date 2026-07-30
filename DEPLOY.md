# Pushing and deploying

## 0. Rotate the leaked key first

`backend/.env.example` in the previous version contained a real Gemini API key
rather than a placeholder, and `.env.example` is a file that gets committed by
design. Revoke that credential before you push anything.

```bash
git log --all --oneline -- backend/.env backend/.env.example
```

Anything returned means the value is in the repository's history and should be
treated as public. Revoking is sufficient — rewriting history only helps for
credentials you cannot revoke.

The rebuilt backend uses no API key at all, so this exposure does not recur. CI
now fails the build if a key-shaped string or a tracked `.env` appears again.

## 1. Push

The two folders replace your existing `backend/` and `frontend/` wholesale, so
delete the old ones rather than merging into them — otherwise the removed modules
(`services/gemini.py`, `services/rag_service.py`, `state.py`, and the rest) stay
behind and confuse the next reader.

```bash
git rm -r --cached backend frontend         # untrack the old trees
# copy the new backend/ and frontend/ into place, then:
git add -A
git status                                  # confirm no .env, no venv, no node_modules
git commit -m "Rebuild backend and frontend: deterministic query engine, no external model"
git push
```

If `git status` lists `backend/venv/` or `frontend/node_modules/`, the old repo
committed them. Untrack them now — they are the reason a clone takes minutes:

```bash
git rm -r --cached backend/venv frontend/node_modules
git commit -m "Untrack virtualenv and node_modules"
```

## 2. Backend hosting — the constraint that decides everything

**DuckDB is a file, and one process may write to it.** Two consequences:

| Requirement | Why |
|---|---|
| A **persistent disk** | An ephemeral filesystem discards every ingested workbook on each redeploy. On a free tier this looks like "my data keeps disappearing". |
| **Exactly one instance, one worker** | A second process cannot open the database file. Autoscaling will not fail gracefully; it will fail to start. |

That rules out serverless. **Vercel cannot host this backend** — no persistent
filesystem and no long-lived process. Vercel is right for the frontend only.

Workable targets: Render or Railway with a mounted disk, Fly.io with a volume, or
a departmental VM. `backend/Dockerfile` runs on all of them unchanged.

### Render

`render.yaml` at the repository root is a blueprint Render reads directly. The
parts that matter:

```yaml
plan: starter          # the free plan has no persistent disk
numInstances: 1
disk:
  mountPath: /data
  sizeGB: 10
```

Set one environment variable by hand in the dashboard (it is marked
`sync: false` so it is never committed):

```
IMDQ_CORS_ORIGINS = ["https://your-frontend.vercel.app"]
```

A JSON array of exact origins. `["*"]` is rejected at startup by design, so a
wildcard stops the service rather than shipping an open CORS policy.

Verify after the first deploy:

```bash
curl https://your-backend.onrender.com/health
```

`"engine": "duckdb"` is the line to check. If it says `sqlite`, DuckDB failed to
install and your data is going somewhere other than the warehouse.

## 3. Frontend on Vercel

Root directory `frontend`, framework Vite — `vercel.json` sets the rest.

One environment variable, and it is the one people miss:

```
VITE_API_BASE = https://your-backend.onrender.com
```

**The `/api` proxy in `vite.config.js` is development-only.** Without
`VITE_API_BASE` the production build calls `/api/...` on the Vercel domain, which
returns Vercel's own 404 page, and the app shows "Backend offline" with nothing in
the network tab that looks like an error.

Vite inlines `VITE_*` variables at build time, so changing it requires a redeploy,
not a restart.

## 4. Order of operations

1. Deploy the backend, note its URL.
2. Set `VITE_API_BASE` on Vercel to that URL, deploy the frontend, note its URL.
3. Set `IMDQ_CORS_ORIGINS` on the backend to the frontend URL. Redeploy the backend.

Step 3 is separate because each side needs the other's final URL, and a browser
CORS failure looks identical to the backend being down.

## 5. Before this holds departmental data

Two things this bundle does not do, both of which matter more than the deployment:

- **No authentication.** Anyone with the URL can upload and query. Put SSO or an
  IP allow-list in front of it before real data goes in.
- **No backups.** The persistent disk is a single copy. `warehouse.duckdb` and
  `lexicon.sqlite` are ordinary files — a scheduled copy off the volume is enough.

And a question worth asking your SATMET contact rather than assuming: whether IMD
observational data may sit on third-party infrastructure at all. If not, the
Dockerfile runs unchanged on a departmental VM, which is also where the
no-external-API property earns its keep.
