# Upgrading

## 1.0 → 1.1: pseudonyms

### What changes

| | |
|---|---|
| **New feature** | Optional [pseudonyms](docs/pseudonyms.md). **Off by default**: until you turn it on, readers see no difference. |
| **Database** | Migrated automatically at start, from schema version 1 to 2. Adds a `pseudonyms` table and a `pseudonym_id` column on `comments`. Existing comments are not touched; their new column is empty, so none of them can ever show as verified. |
| **`widget.js`** | Changed. If your blog pins it with a Subresource Integrity hash, update the hash (step 5), or comments stop loading. |
| **`afterword.css`** | New rules for the ✓ mark and the pseudonym controls. Only matters if you copied the file into your blog's CSS. |
| **Configuration** | No new environment variables. `compose.yaml`, `Dockerfile` and the systemd unit are unchanged, so your edited copies keep working. |
| **Rolling back** | 1.0 runs on a 1.1 database as it is. It ignores the new table and column, so you do not need to restore a backup to go back. |

Downtime is the few seconds it takes to restart the container. The migration
itself takes well under a second, even with many thousands of comments.

### Upgrade with Docker Compose

Run everything from the folder that holds your `compose.yaml` (the Afterword
checkout). The service is called `afterword`, as in the shipped `compose.yaml`.

**1. Check what is running**

```sh
docker compose exec afterword afterword version     # 1.0.0
```

**2. Back up the database, and copy the backup out of the container**

```sh
docker compose exec afterword afterword backup /data/afterword-1.0-backup.db
docker compose cp afterword:/data/afterword-1.0-backup.db ./afterword-1.0-backup.db
```

`afterword backup` uses SQLite's online backup, so the copy is consistent
while the server keeps running. It refuses to overwrite an existing file. On
Docker Compose versions without `cp`, use
`docker cp "$(docker compose ps -q afterword)":/data/afterword-1.0-backup.db .`

The backup contains email addresses and the server's secret key. Keep it
private, and delete it once you are happy with the upgrade.

**3. Get version 1.1**

To test the branch before it is merged:

```sh
git fetch origin
git switch claude/anonymous-persistent-identities-csjlr3
```

After it is merged, use `git switch master && git pull` instead.

If you edited `compose.yaml` in place (for example to set
`AFTERWORD_PUBLIC_URL`), Git keeps your edit, because this version does not
change that file. Check with `git status`.

**4. Rebuild and restart**

```sh
docker compose build --pull
docker compose up -d
```

Compose replaces the container and keeps the `afterword-data` volume. The new
version migrates the database as it starts. Check that everything is in order:

```sh
docker compose ps                                    # "healthy" after about 30 seconds
docker compose logs --tail=20 afterword              # "Afterword 1.1.0 listening on …", no tracebacks
docker compose exec afterword afterword version      # 1.1.0
docker compose exec afterword python -c "import sqlite3; print(sqlite3.connect('/data/afterword.db').execute('PRAGMA user_version').fetchone()[0])"
                                                     # 2
```

Then open the dashboard and a post on your blog. Comments load and can be sent
exactly as before.

**5. If your blog pins the widget with Subresource Integrity**

The widget changed, so its hash changed too.

- If you used the WriteFreely helper with `--sri`, run it again and restart
  WriteFreely:
  `python3 contrib/writefreely/add-comments.py /path/to/writefreely https://comments.example.com --sri`
- If you pasted the pinned snippet yourself, copy the new one from
  **Add to your blog** in the dashboard.

Without SRI there is nothing to do. Browsers may keep the old widget for up to
an hour (`Cache-Control: max-age=3600`). The old widget works with the new
server, but it shows no pseudonym controls or marks.

**6. Turn pseudonyms on, when you want them**

In the dashboard, go to **Settings → The comment form → Let readers keep a name
as a verified pseudonym → Save settings**. Reload a post on your blog. Existing
comments now show **unverified**, and the form offers **Keep this name as my
pseudonym**. If you copied `afterword.css` into your blog's CSS, add the new
rules from `afterword/static/afterword.css` (the `.afterword-verified`,
`.afterword-unverified` and `.afterword-pseudonym…` blocks). If you link the
file from the comment server, you get them automatically.

**7. Remove the backup from the volume**

Once you are satisfied, remove the backup copy inside the volume. Keep the
copy outside it somewhere private for a while.

```sh
docker compose exec afterword rm /data/afterword-1.0-backup.db
```

### Rolling back (Docker Compose)

**Keep everything written since the upgrade (usually what you want).**
1.0 simply ignores the pseudonym data:

```sh
git switch master          # before the merge; afterwards: git switch --detach <the 1.0 commit>
docker compose build
docker compose up -d
```

While 1.0 runs, no comment shows a ✓ mark and names are not reserved. Upgrade
again and everything is back, including the marks on comments that were posted
with a key.

**Return to the exact state before the upgrade.** This loses every comment and
change made since the backup:

```sh
docker compose stop afterword
docker compose run --rm --no-deps --user root -v "$PWD:/restore:ro" afterword sh -c \
  'cp /restore/afterword-1.0-backup.db /data/afterword.db &&
   rm -f /data/afterword.db-wal /data/afterword.db-shm &&
   chown 10001:10001 /data/afterword.db && chmod 600 /data/afterword.db'
git switch master
docker compose build
docker compose up -d
```

Removing the `-wal` and `-shm` files matters. Otherwise SQLite would try to
apply leftovers from the newer database to the restored file.

### Trying 1.1 on a copy first (optional)

You can run 1.1 next to your live 1.0 container, on a copy of your data, and
try pseudonyms on a local test page before touching the live service.

```sh
git clone --branch claude/anonymous-persistent-identities-csjlr3 \
    https://github.com/LoredCast/afterword afterword-trial
cd afterword-trial
cp ../afterword/afterword-1.0-backup.db trial.db      # the backup from step 2
```

Save this as `compose.trial.yaml` in that folder. It is a complete file, not an
override. It uses its own project name, port and volume, so it cannot touch the
live service:

```yaml
name: afterword-trial
services:
  afterword:
    build: .
    environment:
      AFTERWORD_PUBLIC_URL: http://127.0.0.1:8081
      AFTERWORD_DEV: "1"              # plain-http sign-in; only for this local trial
      AFTERWORD_TRUSTED_PROXY: ""
      AFTERWORD_LAYA_INSTALL: "0"
    ports:
      - "127.0.0.1:8081:8080"
    volumes:
      - trial-data:/data
volumes:
  trial-data:
```

Load the copy into the trial volume and start it:

```sh
docker compose -f compose.trial.yaml build
docker compose -f compose.trial.yaml run --rm --no-deps --user root -v "$PWD:/restore:ro" afterword \
  sh -c 'cp /restore/trial.db /data/afterword.db && chown -R 10001:10001 /data && chmod 600 /data/afterword.db'
docker compose -f compose.trial.yaml up -d
```

Now try it out:

1. Sign in at <http://127.0.0.1:8081/admin> with your usual dashboard password.
   It is part of the copied database.
2. In **Settings**, add `http://127.0.0.1:8000` to **Blog address** and tick
   **Let readers keep a name as a verified pseudonym**. Save.
3. Save this as `trial.html`. Use a real post id from your blog to see its
   existing comments; in WriteFreely, that is the `data-thread` value in a
   post's page source.

   ```html
   <!doctype html><meta charset="utf-8"><title>Afterword trial</title>
   <h1>Trial post</h1>
   <section data-afterword data-thread="PUT-A-POST-ID-HERE"></section>
   <script src="http://127.0.0.1:8081/widget.js" defer></script>
   <link rel="stylesheet" href="http://127.0.0.1:8081/afterword.css">
   ```

4. Serve it with `python3 -m http.server 8000 --bind 127.0.0.1` and open
   <http://127.0.0.1:8000/trial.html>.
5. Things to try:
   - Old comments show **unverified**.
   - Tick **Keep this name as my pseudonym** and post.
   - Publish the comment in the trial dashboard. It shows **✓ verified
     pseudonym**.
   - In a private window, posting under the same name, or `NAME` in capitals,
     is refused.
   - Open **Restore a pseudonym from a backup key**, paste the key and post
     again.
   - The **Pseudonyms** page in the dashboard lists the name, and **Release**
     works.

If the server is remote, forward both ports first:
`ssh -L 8081:127.0.0.1:8081 -L 8000:127.0.0.1:8000 you@your-server`.

Clean up afterwards. The trial copy contains email addresses:

```sh
docker compose -f compose.trial.yaml down -v
rm trial.db
```

### Upgrade without Docker (systemd and a virtualenv)

```sh
sudo -u afterword AFTERWORD_DATA=/var/lib/afterword /opt/afterword/venv/bin/afterword \
    backup /var/lib/afterword/afterword-1.0-backup.db
cd /path/to/afterword && git fetch origin && git switch claude/anonymous-persistent-identities-csjlr3
sudo /opt/afterword/venv/bin/pip install /path/to/afterword
sudo systemctl restart afterword
sudo journalctl -u afterword -n 20                 # "Afterword 1.1.0 listening on …"
```

Then carry on with steps 5 and 6 above. To roll back, install the 1.0 checkout
the same way and restart. The database needs no change.
