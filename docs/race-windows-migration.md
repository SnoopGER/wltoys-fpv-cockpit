# Race Windows Migration Notes

This branch captures the Windows migration baseline for `race.zen-rc.net`.

## Verified Baseline

- Windows native Python dashboard starts from `.venv`.
- Local dashboard works at `http://127.0.0.1:5555`.
- LAN dashboard works at the current Windows LAN address.
- Cloudflare public route works at `https://race.zen-rc.net`.
- Discord OAuth works through Cloudflare, LAN, localhost, and `127.0.0.1` when the matching redirect URLs are present in `.env.local` and in the Discord Developer Portal.
- Manual car WiFi connection through the Windows USB WiFi stick works.
- Car wake/connect works before the car sleep timer.
- Video streaming is smooth.
- Car control works.

## Local Secrets

Runtime secrets are intentionally not committed. The app loads:

```text
.env.local
```

The required non-secret shape is:

```text
PUBLIC_BASE_URL=https://race.zen-rc.net
DISCORD_REDIRECT_URI=https://race.zen-rc.net/auth/discord/callback
DISCORD_PUBLIC_REDIRECT_URI=https://race.zen-rc.net/auth/discord/callback
DISCORD_REDIRECT_URIS=https://race.zen-rc.net/auth/discord/callback,http://<windows-lan-ip>:5555/auth/discord/callback,http://localhost:5555/auth/discord/callback,http://127.0.0.1:5555/auth/discord/callback
```

Also keep `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `SESSION_SECRET`, `ADMIN_DISCORD_IDS`, and `ALLOWED_DISCORD_IDS` local only.

## Cloudflare

The `race.zen-rc.net` public hostname should route to:

```text
HTTP -> 127.0.0.1:5555
```

Leave the Cloudflare public hostname `Path` field empty so all app routes are exposed, including `/`, `/login`, `/auth/discord/callback`, `/api/status`, and Socket.IO paths.

## Control BAT

Use:

```text
FPV-Dashboard-Control.bat
```

Menu features:

- Status
- Start dashboard
- Stop dashboard
- Restart dashboard
- Open local dashboard
- Open public dashboard
- Show latest logs
- Restart Cloudflared service
- LOCAL TESTMODE: stop Cloudflare tunnel
- LIVE MODE: start Cloudflare tunnel

Direct command examples:

```powershell
.\FPV-Dashboard-Control.bat status
.\FPV-Dashboard-Control.bat start
.\FPV-Dashboard-Control.bat stop
.\FPV-Dashboard-Control.bat restart
.\FPV-Dashboard-Control.bat local
.\FPV-Dashboard-Control.bat live
.\FPV-Dashboard-Control.bat logs
```

Starting or stopping the `Cloudflared` Windows service usually requires an Administrator shell.
