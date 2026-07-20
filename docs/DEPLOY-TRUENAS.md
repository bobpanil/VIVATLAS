# Deploying VivAtlas on TrueNAS (Docker)

VivAtlas ships as a single container. All mutable state — the SQLite database and
a generated `secret_key` — lives in one mounted folder (`/data`). Back that folder
up and you've backed up everything.

These steps target **TrueNAS SCALE Electric Eel (24.10) or newer**, which runs
Docker under the hood and can install a "Custom App" from a Compose file.

---

## 1. Build the image

TrueNAS installs images, it doesn't build them, so build `vivatlas:latest` first.
Because the repo is private, the simplest path is to build **on the NAS itself**
over SSH — no registry required.

```sh
# SSH into TrueNAS as an admin user, then:
sudo docker build -t vivatlas:latest "https://<YOUR_GITHUB_TOKEN>@github.com/bobpanil/vivatlas.git#main"
```

`docker build <git-url>` clones the repo and builds its root `Dockerfile`. The
`.dockerignore` keeps `secrets.md`, `.env`, and any local database out of the image.

<details>
<summary>Alternative: build elsewhere and push to a registry</summary>

On any machine with Docker (e.g. your Windows box with Docker Desktop):

```sh
git clone https://github.com/bobpanil/vivatlas.git && cd vivatlas
docker build -t ghcr.io/bobpanil/vivatlas:latest .
echo "$GITHUB_TOKEN" | docker login ghcr.io -u bobpanil --password-stdin
docker push ghcr.io/bobpanil/vivatlas:latest
```

Then use that full image name in step 3, and add the registry credentials under
TrueNAS **Apps → Discover → (gear) → Manage Container Images** so the pull works.
</details>

---

## 2. Create a dataset for the data

TrueNAS UI → **Datasets** → create e.g. `your-pool/apps/vivatlas/data`.

The container runs as **UID 1000**, so that folder must be writable by UID 1000.
From SSH:

```sh
sudo chown -R 1000:1000 /mnt/your-pool/apps/vivatlas/data
```

---

## 3. Install as a Custom App (Compose)

TrueNAS UI → **Apps → Discover Apps → Custom App → Install via YAML**, and paste:

```yaml
services:
  vivatlas:
    image: vivatlas:latest
    pull_policy: never          # use the image we built locally; don't try a registry
    restart: unless-stopped
    ports:
      - "8710:8710"             # http://<truenas-ip>:8710  (change host port if needed)
    environment:
      # Recommended: set a fixed key once and never change it. Generate with:
      #   python3 -c "import secrets; print(secrets.token_hex(32))"
      # If left empty, a stable key is generated and stored in the data folder.
      SECRET_KEY: ""
      DATABASE_URL: "sqlite:////data/vivatlas.db"
      VIVATLAS_SEED: "0"        # set "1" for the FIRST run to load ~200 demo cards
    volumes:
      - type: bind
        source: /mnt/your-pool/apps/vivatlas/data
        target: /data
```

Save. TrueNAS starts the container; the entrypoint runs `init-db` automatically,
then launches the server on port 8710.

> If TrueNAS insists on pulling and errors on `vivatlas:latest`, confirm the image
> exists with `sudo docker images | grep vivatlas` and that `pull_policy: never` is set.

---

## 4. First run — become the owner

Open `http://<truenas-ip>:8710/` in a browser. It redirects to **`/setup`**.
The first account you create there becomes the **owner** (admin). Do this promptly —
until an owner exists, anyone reaching the page could claim it.

---

## 5. Demo data (optional)

Two ways to load the ~200 sample cards (real GitHub repositories):

- **At deploy:** set `VIVATLAS_SEED: "1"` for the first start, then edit it back to
  `"0"` (seeding is idempotent, but there's no reason to re-run it every boot).
- **Anytime:** exec into the running container:
  ```sh
  sudo docker exec -it vivatlas python /app/scripts/seed_mock.py
  # remove them again with:
  sudo docker exec -it vivatlas python /app/scripts/seed_mock.py --wipe
  ```

The demo cards live under a single "GitHub (demo data)" source, so `--wipe` only
ever removes demo cards — never anything you add yourself.

---

## 6. Updating to a new version

```sh
sudo docker build -t vivatlas:latest "https://<TOKEN>@github.com/bobpanil/vivatlas.git#main"
```

Then in the app's page choose **Restart** (or **Stop** → **Start**). On start the
entrypoint runs `init-db`, which adds any new database columns before serving — so
upgrades don't break on an older database.

---

## 7. Notes

- **SECRET_KEY** signs sessions and encrypts stored tokens. Keep it constant;
  changing it logs everyone out and makes saved integration tokens unreadable. The
  auto-generated key persists in `/data/secret_key`, so it already survives restarts.
- **Backups:** snapshot or copy the `.../vivatlas/data` dataset. That's the whole
  application state (database + key). Avatars and previews are inside the database.
- **HTTPS / remote access:** the container serves plain HTTP on 8710. To reach it
  from outside your LAN, put it behind a reverse proxy (Traefik, Nginx Proxy
  Manager, Caddy) or a tunnel that terminates TLS — don't expose 8710 directly.
- **Email, Gitea/GitHub sources, AI keys:** all optional and configurable later from
  the in-app **Admin → Integrations** panel; nothing extra is needed to boot.
