# How to Docker Deploy FPV Dashboard V2 on Windows

This guide is for deploying **FPV Dashboard V2** — the version with **Discord login, guest drive codes, mobile controls, and the admin panel** — on a Windows machine using Docker Desktop.

## Short answer

Yes: Docker Desktop on Windows can download/build this project and run it as a **Linux container**.

However, there is one important FPV-car caveat:

- The **web server/admin panel/Discord login** should run fine in Docker Desktop.
- The **car UDP hardware path** may need testing because Docker Desktop uses NAT/WSL2 networking, not true Linux host networking.
- On native Linux, we use `network_mode: host`, which is ideal for the car.
- On Windows Docker Desktop, true host networking is limited/version-dependent. Explicit UDP/TCP port mapping may work, but the car may still send UDP replies to the wrong network address depending on Docker/WSL routing.

If Docker networking fails for live car video/control, the reliable fallback is running the Python app directly on Windows or on a native Linux box connected to the car network.

---

## 1. Install prerequisites on Windows

Install these on the Windows server:

1. **Docker Desktop**
   - <https://www.docker.com/products/docker-desktop/>
   - Use the WSL2 backend.
   - Make sure Docker Desktop is running before starting the app.

2. **Git for Windows**
   - <https://git-scm.com/download/win>

3. Optional but useful:
   - Windows Terminal
   - VS Code

---

## 2. Clone the V2 dashboard from GitHub

Open **PowerShell** and run:

```powershell
cd C:\
git clone --branch codex-discord-race-lobby https://github.com/SnoopGER/wltoys-fpv-cockpit.git fpv-dashboard-v2
cd C:\fpv-dashboard-v2
```

If you already cloned it before, update it instead:

```powershell
cd C:\fpv-dashboard-v2
git fetch origin
git checkout codex-discord-race-lobby
git pull origin codex-discord-race-lobby
```

---

## 3. Create `.env.local`

Create a file named:

```text
C:\fpv-dashboard-v2\.env.local
```

PowerShell command:

```powershell
notepad .env.local
```

Paste this template and replace the placeholder values:

```bash
# Discord OAuth
DISCORD_CLIENT_ID=your_discord_client_id
DISCORD_CLIENT_SECRET=your_discord_client_secret

# Public URL used by Discord OAuth and external users.
# For the live track setup this should usually be the Cloudflare/domain URL.
PUBLIC_BASE_URL=https://race.zen-rc.net

# Discord redirect URLs.
# These must also be configured in the Discord Developer Portal.
DISCORD_REDIRECT_URI=https://race.zen-rc.net/auth/discord/callback
DISCORD_REDIRECT_URIS=https://race.zen-rc.net/auth/discord/callback,http://localhost:5555/auth/discord/callback
DISCORD_PUBLIC_REDIRECT_URI=https://race.zen-rc.net/auth/discord/callback

# Flask session secret. Generate a long random value and keep it private.
SESSION_SECRET=replace_with_a_long_random_secret

# Discord access control
ADMIN_DISCORD_IDS=your_discord_user_id
ALLOWED_DISCORD_IDS=your_discord_user_id

# Lobby settings
DEFAULT_DRIVE_SECONDS=120
MAX_REMOTE_SPEED_PERCENT=70

# FPV car network
FPV_CAR_IP=172.16.11.1
FPV_LISTEN_PORT=1234
```

Generate a session secret in PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

If Python is not installed on Windows, you can generate one inside Docker later:

```powershell
docker run --rm python:3.13-slim python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the output into `SESSION_SECRET=`.

Important:

- Do **not** commit `.env.local`.
- Do **not** paste secrets into GitHub issues, Discord, or public logs.
- Discord callback URLs must exactly match what is configured in the Discord Developer Portal.

---

## 4. Create local runtime data folder

This folder stores guest codes and ban state outside the container so they survive restarts.

```powershell
mkdir data
```

The container will use:

```text
/app/data/guest_codes.json
/app/data/banned_discord_ids
```

Mapped to Windows:

```text
C:\fpv-dashboard-v2\data\guest_codes.json
C:\fpv-dashboard-v2\data\banned_discord_ids
```

---

## 5. Windows Docker Desktop compose file

The repo's main `docker-compose.yml` is optimized for native Linux host networking. For Windows Docker Desktop, create a local override file named:

```text
C:\fpv-dashboard-v2\docker-compose.windows.yml
```

PowerShell:

```powershell
notepad docker-compose.windows.yml
```

Paste this:

```yaml
services:
  fpv-dashboard-v2:
    build:
      context: .
      dockerfile: Dockerfile
    image: fpv-dashboard-v2:latest
    container_name: fpv-dashboard-v2

    env_file:
      - .env.local

    environment:
      GUEST_CODES_FILE: /app/data/guest_codes.json
      BANNED_DISCORD_IDS_FILE: /app/data/banned_discord_ids
      PYTHONUNBUFFERED: "1"

    ports:
      - "5555:5555/tcp"       # Web UI / Discord callback / admin panel
      - "1234:1234/udp"       # FPV video UDP listen port
      - "23458:23458/udp"     # Car motor/control UDP
      - "23459:23459/udp"     # Car handshake UDP

    volumes:
      - ./data:/app/data

    extra_hosts:
      - "host.docker.internal:host-gateway"

    restart: unless-stopped
    stop_grace_period: 10s
```

Why this file is separate:

- Linux deployment uses `network_mode: host`.
- Docker Desktop on Windows usually needs explicit `ports:` mappings.
- This file is local/deployment-specific and can be recreated from this guide.

---

## 6. Build and start the container

From PowerShell:

```powershell
cd C:\fpv-dashboard-v2
docker compose -f docker-compose.windows.yml up -d --build
```

Check logs:

```powershell
docker compose -f docker-compose.windows.yml logs -f fpv-dashboard-v2
```

Open locally:

```text
http://localhost:5555
```

If using the public domain/tunnel:

```text
https://race.zen-rc.net
```

---

## 7. Stop, restart, update

Stop:

```powershell
docker compose -f docker-compose.windows.yml down
```

Restart:

```powershell
docker compose -f docker-compose.windows.yml restart
```

Update from GitHub and rebuild:

```powershell
cd C:\fpv-dashboard-v2
git pull origin codex-discord-race-lobby
docker compose -f docker-compose.windows.yml up -d --build
```

View running containers:

```powershell
docker ps
```

---

## 8. Cloudflare / public URL note

For the track server, the public domain/tunnel should point to the Windows machine on port `5555`:

```text
http://localhost:5555
```

or, if the tunnel runs elsewhere on the LAN:

```text
http://WINDOWS_SERVER_LAN_IP:5555
```

Discord Developer Portal must include the public callback:

```text
https://race.zen-rc.net/auth/discord/callback
```

Optional local callback for testing:

```text
http://localhost:5555/auth/discord/callback
```

Both should also be present in `.env.local` under `DISCORD_REDIRECT_URIS`.

---

## 9. Verify the dashboard

After the container starts, test the basic HTTP app:

```powershell
curl http://localhost:5555/api/status
```

Expected: JSON status output.

Then open:

```text
http://localhost:5555
```

Check:

- Discord login button works.
- Admin account becomes admin after login.
- Admin panel appears.
- Guest code UI appears.
- Public URL redirects back correctly after Discord OAuth.

---

## 10. Verify the car UDP path

Important: this is the part that may or may not work perfectly through Docker Desktop networking.

Before testing:

1. Make sure the Windows server is connected to the FPV car WiFi/network.
2. Make sure no old Python version of the dashboard is running at the same time.
3. Make sure only one container/app instance is trying to talk to the car.

Check status:

```powershell
curl http://localhost:5555/api/status
```

In the web UI:

1. Open the dashboard.
2. Connect to the car.
3. Check whether video appears.
4. Test control carefully with wheels off the ground first.

If video/control does not work:

- Docker Desktop NAT/WSL routing is probably blocking the UDP hardware path.
- The web app itself may still be working correctly.
- Try native Linux Docker with `network_mode: host`, or run the Python app directly on the machine connected to the car network.

---

## 11. Important: do not run two dashboard instances

Do not run the old Python app and the Docker container at the same time.

The car uses fixed UDP ports. Two dashboard instances can confuse the car and break video/control.

On Windows, check for old Python processes:

```powershell
Get-Process python -ErrorAction SilentlyContinue
```

Stop a known old Python process only if you are sure it is the old dashboard:

```powershell
Stop-Process -Name python -Force
```

Safer option: close the old terminal/window that started the Python app.

---

## 12. Troubleshooting

### Container fails immediately

Check logs:

```powershell
docker compose -f docker-compose.windows.yml logs fpv-dashboard-v2
```

Common causes:

- `.env.local` missing
- bad Discord config
- typo in `docker-compose.windows.yml`
- Docker Desktop not running

### Port 5555 already in use

Find the process:

```powershell
netstat -ano | findstr :5555
```

Then inspect the PID in Task Manager or stop the old app.

### Discord login redirects to the wrong place

Check:

- `PUBLIC_BASE_URL`
- `DISCORD_REDIRECT_URI`
- `DISCORD_REDIRECT_URIS`
- Discord Developer Portal redirect URL list
- Cloudflare/tunnel target points to port `5555`

### Web UI works but car video/control does not

Likely Docker Desktop UDP/NAT routing.

Try:

1. Confirm Windows can reach the car network.
2. Confirm `FPV_CAR_IP=172.16.11.1`.
3. Confirm no second dashboard instance is running.
4. Try running on native Linux Docker with `network_mode: host`.
5. Fallback: run directly with Python on the machine connected to the car network.

---

## 13. Native Linux deployment command for comparison

On a real Linux track server, use the repo's normal compose file:

```bash
git clone --branch codex-discord-race-lobby https://github.com/SnoopGER/wltoys-fpv-cockpit.git fpv-dashboard-v2
cd fpv-dashboard-v2
nano .env.local
chmod 600 .env.local
mkdir -p data
docker compose up -d --build
```

This is the most reliable Docker mode for the FPV car because Linux supports `network_mode: host` properly.

---

## 14. Quick command summary for Windows

```powershell
cd C:\
git clone --branch codex-discord-race-lobby https://github.com/SnoopGER/wltoys-fpv-cockpit.git fpv-dashboard-v2
cd C:\fpv-dashboard-v2
notepad .env.local
mkdir data
notepad docker-compose.windows.yml
docker compose -f docker-compose.windows.yml up -d --build
docker compose -f docker-compose.windows.yml logs -f fpv-dashboard-v2
```

Open:

```text
http://localhost:5555
```

Public route:

```text
https://race.zen-rc.net
```
