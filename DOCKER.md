# Optional: run with Docker (local only)

**Preferred share path:** clone this repo from GitHub and run with Python
(see the main [README](README.md)). Use Docker only if you want a one-command
runtime without installing Python.

Each person still runs the CRM **on their own computer**. Data stays in Docker
volumes — nothing is uploaded to a cloud CRM.

## First run

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Copy `docker-compose.yml` from this repo (or clone the repo).
3. In that folder:

```bash
docker compose up -d
```

4. Open [http://127.0.0.1:8765](http://127.0.0.1:8765).
5. Go to **Settings** and fill your Ideal Customer Profile (who you sell to).

## Update to a new published version

```bash
docker compose pull
docker compose up -d
```

Your `data` / `inbox` / `backups` volumes are kept.

## Build from source (no registry)

```bash
docker compose up -d --build
```

## Notes

- Still a **single-user, local** app.
- Do not publish port `8765` to the public internet.
- Image name defaults to `ghcr.io/gusaga/prospects:latest` — change in
  `docker-compose.yml` if your GitHub repo path differs.
