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

Visitors come in through a Cloudflare Tunnel: `cloudflared` dials out, so no
port is open on the machine or the router.

## Files

| file | what |
|---|---|
| `compose.yml` | the app and `cloudflared`; neither publishes a port |
| `update.sh` | pull, restart, health check, roll back, seed |
| `vivatlas-demo-update.service`, `.timer` | run `update.sh` every 15 minutes |
| `links.txt` | the starter catalogue: public open-source projects only |

## Where it runs

Strangers get accounts here, so give it a machine of its own: a VM, not a
container on a server that matters. From the VM, only the internet should be
reachable. Block the home network, the host, the router and any tailnet
outside the VM, where root inside it can't undo the rules. The VM holds no
keys to anything else.

## The tunnel

In Cloudflare Zero Trust, go to **Networks > Tunnels > Create a tunnel >
Cloudflared**. Use a tunnel of its own, so its token can only ever serve the
demo. Copy the token. Add a public hostname pointing at
`http://vivatlas:8710`.

## Install on the VM

On Debian with Docker installed, from a copy of this folder:

```sh
sudo mkdir -p /opt/vivatlas-demo
sudo cp compose.yml update.sh links.txt /opt/vivatlas-demo/
sudo install -m 600 /dev/null /opt/vivatlas-demo/.env
sudoedit /opt/vivatlas-demo/.env       # one line: TUNNEL_TOKEN=<the token>
sudo cp vivatlas-demo-update.service vivatlas-demo-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vivatlas-demo-update.timer
sudo /opt/vivatlas-demo/update.sh     # the first start, without waiting for the timer
```

## Watching it

```sh
journalctl -u vivatlas-demo-update -n 50      # what the updates did
cd /opt/vivatlas-demo && sudo docker compose ps
cd /opt/vivatlas-demo && sudo docker compose logs --tail 50 vivatlas cloudflared
```

After editing `links.txt`, add the new links without waiting for a release:

```sh
cd /opt/vivatlas-demo && sudo docker compose exec vivatlas python -m vivatlas.demo seed /seed/links.txt
```

Don't run a plain `docker compose up -d` after a rollback. It would start the
image that just failed. Run `update.sh` instead.

`cloudflared` is pinned to a version in `compose.yml`. To move it, change the
version there and run `sudo docker compose up -d cloudflared`.

To take the demo offline at once, remove the public hostname from the tunnel
in Cloudflare.
