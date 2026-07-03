# Migration: Screensaver → Mosaic

The app was renamed from **Screensaver** to **Mosaic** (commit `6a7f46e`). The rename
is almost entirely cosmetic, but it rotated the Argon2id **salt** used to derive the
Fernet key that encrypts secrets at rest. Those secrets must be re-encrypted with a
one-shot script before the renamed app is used, or the app will read them as empty.

This is a one-time migration. Downtime is ~1–2 minutes.

## What needs migrating (and what doesn't)

Only three SQLite files hold encrypted data, all in the bind-mounted `config/` dir
(`compose.yaml` mounts `./config:/app/config`):

| File | Encrypted field | Holds |
|---|---|---|
| `config/settings.db` | `settings.value` where key=`ha_token` | Home Assistant long-lived token |
| `config/immich_pulls.db` | each `pulls.config_json` → `.api_key` | Immich API keys, per pull |
| `config/api_keys.db` | `api_keys.enc_secret` | the recoverable `sk_…` for each API key |

Everything else — display groups, curation, endpoints, thumbnails, images — is **not**
encrypted and needs no migration. If you have no API keys / no HA token / no Immich
pulls, the script reports `0` and is a safe no-op. Run it anyway.

## Preflight: which secret encrypts your data

The Fernet key is derived from a secret resolved in this order (the rename did **not**
change it — only the salt): `API_KEY_ENCRYPTION_SECRET` → `SESSION_SECRET` →
`config/secret.key`. The migration must run with the same secret the app uses.

```bash
grep -E 'SESSION_SECRET|API_KEY_ENCRYPTION_SECRET' .env ; ls -l config/secret.key
```

- Nothing printed by the grep and `secret.key` exists → the secret is **`config/secret.key`**
  (the common case with OIDC disabled). **Do not delete or regenerate `secret.key`** — both
  the migration and the renamed app need it.
- Either env var is set → the migration must see it too. Running the migration via
  `docker compose run` handles this automatically (it inherits `env_file: .env`); running
  it on the host requires loading the env first (see below).

## Run the migration, in order

The app must be **stopped** during the migration (SQLite WAL write safety), and the
migration must complete **before the renamed app starts**. The new code uses the new salt;
if it boots against un-migrated data it reads those secrets as empty. It won't corrupt
anything (`decrypt_secret` returns `None` rather than raising) — just don't save settings
in that state.

```bash
# 0. From the project directory. Belt-and-suspenders backup of config
#    (the script also writes its own *.db.bak files).
cp -a config config.bak-$(date +%s)

# 1. Stop the OLD container. It was named "screensaver"; the renamed compose
#    won't find it by the new name, so stop it explicitly.
docker rm -f screensaver 2>/dev/null || docker compose down

# 2. Move the on-disk image data to the path the renamed compose expects.
#    (thumbnails are a regenerable cache — moving them is fine either way.)
mv /mnt/usb-storage-1/screensaver /mnt/usb-storage-1/mosaic

# 3. Pull the rename (new compose, code, and the migration script).
git pull

# 4. Build the new image (also makes the script runnable inside it).
docker compose build

# 5. DRY RUN inside a one-off container — inherits the same env_file (.env) and
#    ./config mount as the app, so it resolves the identical secret. No host
#    Python needed.
docker compose run --rm --no-deps mosaic \
    python scripts/migrate_rebrand_secrets.py --config-dir config --dry-run

# 6. If the counts look right (see "Reading the output"), run for real.
docker compose run --rm --no-deps mosaic \
    python scripts/migrate_rebrand_secrets.py --config-dir config

# 7. Start the renamed app.
docker compose up -d
```

### Running on the host instead of in a container

```bash
pip install argon2-cffi cryptography
# Only if you use SESSION_SECRET / API_KEY_ENCRYPTION_SECRET:
set -a; source .env; set +a
python3 scripts/migrate_rebrand_secrets.py --config-dir config --dry-run
python3 scripts/migrate_rebrand_secrets.py --config-dir config
```

## Reading the output

The script ends with three counts:

- **`Re-encrypted`** — secrets moved old-salt → new-salt. Should equal
  (API keys with recoverable secrets) + (1 if an HA token is set) + (Immich pulls with keys).
- **`Already new`** — already on the new salt. A second run shows everything here — the
  script is **idempotent** and safe to re-run.
- **`Undecryptable`** — decrypts under neither salt. Only happens if a value was encrypted
  under a genuinely different secret (e.g. a `secret.key` that was later replaced). Those
  can't be recovered and must be **re-entered in the admin UI**. `0` means a clean migration.

Backups are written next to each DB as `*.db.bak`.

## Verify

```bash
docker compose logs -f mosaic     # expect "Mosaic Starting", no decrypt errors
```

In the admin UI, without re-entering anything:

- **Settings → Home Assistant → Test connection** passes (HA token decrypts).
- Trigger an **Immich pull** (an Immich key decrypts).
- An existing **API key** still authenticates a display URL.

## Rollback

Nothing destructive happens, so it is fully reversible:

```bash
docker compose down
rm -rf config && mv config.bak-<stamp> config          # restore DBs + secret.key
mv /mnt/usb-storage-1/mosaic /mnt/usb-storage-1/screensaver
git checkout 94838ff                                    # pre-rename commit
docker compose build && docker compose up -d
```

Or, more surgically, restore just the `config/*.db.bak` files the script wrote and re-run
with the old image.

## One-time cosmetic effects (no action needed)

- Everyone is logged out once — the session cookie was renamed
  `screensaver_session` → `mosaic_session`.
- The admin theme resets to default once — the localStorage key was renamed
  `screensaver-theme` → `mosaic-theme`.
- A config-export bundle made **before** the rename shows a "encrypted with a different
  secret" warning if restored **after** the rename. That's a false alarm from the
  fingerprint prefix changing — the secret is unchanged and the data is fine. New exports
  are unaffected.

## External follow-ups (outside the code)

- Repoint DNS / reverse-proxy / TLS cert to the new subdomain
  (e.g. `mosaic.azureolympos.com`). The app builds image links relative to the
  request origin, so there's no `BASE_URL` to update.
- If OIDC is enabled and `OIDC_CLIENT_ID=screensaver`, update the client registration in
  your identity provider and the env var.
- Optionally rename the repo directory and its git remote.
