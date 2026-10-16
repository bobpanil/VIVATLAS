# The public demo

A VIVATLAS anyone can try before installing their own. It runs with `DEMO_MODE`
on (see `src/vivatlas/demo.py`):

- **"Try it"** gives each visitor a private account. The account is deleted after 24 hours.
- **Saves stay private.** Only the owner can share, so visitors only ever see the starter catalogue from `links.txt`.
- **Reviewers' account `demo` / `demo`**, for the browser-extension stores.
- **No AI and no MCP.**

It keeps itself on the newest release: `update.sh` runs every 15 minutes and
moves to the `stable` image when a new `v*` tag is pushed. If the new version
doesn't come up healthy, it goes back to the one before and skips the bad image
until the next release.

## Files

| file | what |
|---|---|
| `compose.yml` | the app (no published port) and Caddy (80/443, HTTPS) |
| `Caddyfile` | the host name from `.env`, proxied to the app |
| `update.sh` | pull, restart, health check, roll back, seed |
| `vivatlas-demo-update.service`, `.timer` | run `update.sh` every 15 minutes |
| `links.txt` | the starter catalogue: public open-source projects only |

## Install on the VM

On Debian with Docker installed, from a copy of this folder:

```sh
sudo mkdir -p /opt/vivatlas-demo
sudo cp compose.yml Caddyfile update.sh links.txt /opt/vivatlas-demo/
echo "DEMO_HOST=34.1.2.3.sslip.io" | sudo tee /opt/vivatlas-demo/.env   # or your own name
sudo cp vivatlas-demo-update.service vivatlas-demo-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vivatlas-demo-update.timer
sudo /opt/vivatlas-demo/update.sh     # the first start, without waiting for the timer
```

## Watching it

```sh
journalctl -u vivatlas-demo-update -n 50      # what the updates did
cd /opt/vivatlas-demo && sudo docker compose ps
cd /opt/vivatlas-demo && sudo docker compose logs --tail 50 vivatlas
```

After editing `links.txt`, add the new links without waiting for a release:

```sh
cd /opt/vivatlas-demo && sudo docker compose exec vivatlas python -m vivatlas.demo seed /seed/links.txt
```

Don't run a plain `docker compose up -d` after a rollback. It would start the
image that just failed. Run `update.sh` instead.
