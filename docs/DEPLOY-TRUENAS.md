# Deploying VIVATLAS on TrueNAS (Docker)

VIVATLAS ships as a single container. All mutable state — the SQLite database and
a generated `secret_key` — lives in one mounted folder (`/data`). Back that folder
up and you've backed up everything.

These steps target **TrueNAS SCALE Electric Eel (24.10) or newer**, which runs
Docker under the hood and can install a "Custom App" from a Compose file.

---

## 1. Get the image

GitHub Actions (`.github/workflows/docker.yml`) builds the image on every push to
`main` and publishes it to the GitHub Container Registry (GHCR). You just pull it —
no building on the NAS:

```
ghcr.io/bobpanil/vivatlas:latest
```

Because the repo is private, that package is private too, so do **one of these once**:

- **Make the package public (simplest).** On GitHub: your profile → **Packages** →
  `vivatlas` → **Package settings** → **Change visibility** → **Public**. Then
  TrueNAS pulls it with no login.
- **Or keep it private and authenticate on TrueNAS.** Create a PAT with the
  `read:packages` scope, then in TrueNAS **Apps → (⋮) → Manage Container Images →
  Add**: registry `ghcr.io`, username `bobpanil`, password = that PAT.

To update later: push to `main` (or run the workflow manually from the repo's
**Actions** tab), wait for the build, then **Pull image** / restart the app on TrueNAS.

<details>
<summary>Alternative: build it yourself on the NAS (no registry)</summary>

```sh
# SSH into TrueNAS as an admin user, then:
sudo docker build -t ghcr.io/bobpanil/vivatlas:latest "https://<YOUR_GITHUB_TOKEN>@github.com/bobpanil/vivatlas.git#main"
```

`docker build <git-url>` clones the repo and builds its root `Dockerfile`. The
`.dockerignore` keeps `secrets.md`, `.env`, and any local database out of the image.
If you build locally, set `pull_policy: never` in the compose so it uses the local image.
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
    image: ghcr.io/bobpanil/vivatlas:latest
    pull_policy: always         # pull the CI-published image from GHCR
    restart: unless-stopped
    ports:
      - "8710:8710"             # http://<truenas-ip>:8710  (change host port if needed)
    environment:
      # Recommended: set a fixed key once and never change it. Generate with:
      #   python3 -c "import secrets; print(secrets.token_hex(32))"
      # If left empty, a stable key is generated and stored in the data folder.
      SECRET_KEY: ""
      DATABASE_URL: "sqlite:////data/vivatlas.db"
      # Only if a proxy in front terminates TLS (Cloudflare, Traefik, NPM…).
      # Without it the container sees plain http and sets cookies without Secure
      # on a site the browser reached over https. Put the address the proxy
      # connects FROM — in Docker usually the bridge gateway:
      #   docker exec vivatlas ip route | awk '/default/ {print $3}'
      TRUSTED_PROXIES: ""
      # Optional: draw a picture for cards whose project offers none. A Google
      # image model needs billing on the project; "pollinations:flux" is free and
      # keyless (soft, generic pictures; card name + summary go to pollinations.ai).
      # Empty = no drawing; those cards get VIVATLAS's own designed cover.
      IMAGE_MODEL: ""
    volumes:
      - type: bind
        source: /mnt/your-pool/apps/vivatlas/data
        target: /data
```

Save. TrueNAS starts the container; the entrypoint runs `init-db` automatically,
then launches the server on port 8710.

> If the pull fails with "denied"/"unauthorized", the GHCR package is still private —
> make it public or add the `read:packages` credential (see step 1).

---

## 4. First run — become the owner

Open `http://<truenas-ip>:8710/` in a browser. It redirects to **`/setup`**.
The first account you create there becomes the **owner** (admin). Do this promptly —
until an owner exists, anyone reaching the page could claim it.

---

## 5. Add your sources

Sign in as the owner, open **admin → Sources**, set your Gitea address/token and/or a
GitHub account, **Save**, then **Scan now**. Public repositories import as shared cards
for everyone; the scan then refreshes daily.

---

## 6. Updating to a new version

Push to `main` (or trigger the workflow from the repo's **Actions** tab). Once the
GitHub Actions build finishes publishing the new `:latest` image, on TrueNAS use the
app's **Pull image** (or **Update**), then **Restart**. On start the entrypoint runs
`init-db`, which adds any new database columns before serving — so upgrades don't
break on an older database.

Two things that bite:

- **Wait for the build.** Pulling before the Actions run is green fetches the
  *previous* image. The run is done when the repo's Actions tab shows it green; the
  running container's build is stamped in **Admin** (`BUILD 1.0.xx · <sha>`), so you
  can confirm which one you actually got.
- **Save may not re-pull.** TrueNAS reuses the existing container when the compose
  text hasn't changed, `pull_policy: always` notwithstanding. Use the explicit
  **Pull image** / **Update** action, then Restart.

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
  When you do, set **`TRUSTED_PROXIES`** to the address the proxy connects from.
  The container cannot otherwise tell that the visitor arrived over https, and
  every cookie it sets goes out without the `Secure` flag. Also set the site
  address in **admin → integrations**, so links in emails and the phone sign-in
  code name the public https URL rather than the container's internal one.
- **Email, Gitea/GitHub sources, AI keys:** all optional and configurable later from
  the in-app **Admin → Integrations** panel; nothing extra is needed to boot.
- **Card pictures** fill themselves in after a deploy — a background pass every few
  seconds while there is work, then every quarter-hour. Nothing to run. The log says
  `previews: N card(s) got a picture` as it goes; `docker exec <container> python -m
  vivatlas.cli previews` forces a full pass if you want it now. Pictures are cached
  by the browser for a day, and their address changes when the picture does, so a
  replaced picture shows without a hard refresh.
- **Phone sign-in by QR** needs the site address set in **Admin → Integrations**
  when VIVATLAS sits behind a proxy or tunnel — the code has to carry the public
  https address, and the container cannot see that address from the inside. Without
  it the page says so instead of showing a code.
