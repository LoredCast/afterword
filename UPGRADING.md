# Upgrading

Every upgrade with Docker Compose follows the same steps, whichever version you
come from. The [version notes](#version-notes) below say what changes in each
release, and anything extra to do. Database migrations run automatically when
the new version starts, and skipping versions (1.0 → 1.2) is fine.

## Upgrade with Docker Compose

Run everything from the folder that holds your `compose.yaml` (the Afterword
checkout). The service is called `afterword`, as in the shipped `compose.yaml`.

**1. Check what is running**

```sh
docker compose exec afterword afterword version     # e.g. 1.1.0
```

**2. Back up the database, and copy the backup out of the container**

```sh
docker compose exec afterword afterword backup /data/afterword-before-upgrade.db
mkdir -p ~/afterword-backups
docker compose cp afterword:/data/afterword-before-upgrade.db ~/afterword-backups/
python3 -c "import sqlite3; print(sqlite3.connect('$HOME/afterword-backups/afterword-before-upgrade.db').execute('PRAGMA integrity_check').fetchone()[0])"
                                                     # ok
```

`afterword backup` uses SQLite's online backup, so the copy is consistent
while the server keeps running. It refuses to overwrite an existing file. On
Docker Compose versions without `cp`, use
`docker cp "$(docker compose ps -q afterword)":/data/afterword-before-upgrade.db ~/afterword-backups/`

The backup contains email addresses and the server's secret key. It is kept
outside the Git checkout on purpose, so it cannot be committed by accident.
Keep it private, and delete it once you are happy with the upgrade.

**3. Get the new version**

```sh
git pull                  # on master
```

If you edited `compose.yaml` in place (for example to set
`AFTERWORD_PUBLIC_URL`), Git keeps your edit as long as the release does not
change that file. None has so far. Check with `git status`. To try a branch
before it is merged, use `git fetch origin && git switch <branch>` instead.

**4. Rebuild and restart**

```sh
docker compose build --pull
docker compose up -d
```

`docker compose up -d` alone is **not** enough: `compose.yaml` builds the image
from the checkout (`build: .`), and Compose keeps using the image it already
built. Build first, or use `docker compose up -d --build`. `--pull` also
fetches the latest `python:3.12-slim` base image with its security updates; if
Docker Hub rate-limits you, leave it out.

Compose replaces the container and keeps the `afterword-data` volume. Downtime
is the few seconds the restart takes; migrations are quick (a test database
with 200,000 comments migrated in a tenth of a second). Check that everything
is in order:

```sh
docker compose ps                                    # "healthy" after about 30 seconds
docker compose logs --tail=20 afterword              # "Afterword 1.2.0 listening on …", no tracebacks
docker compose exec afterword afterword version      # 1.2.0
docker compose exec afterword python -c "import sqlite3; print(sqlite3.connect('/data/afterword.db').execute('PRAGMA user_version').fetchone()[0])"
                                                     # 3 for 1.2 (2 for 1.1)
```

Then open the dashboard and a post on your blog. Comments load and can be sent
as before.

**5. If your blog pins the widget with Subresource Integrity**

Every release so far changed `widget.js`, so its hash changes too.

- If you used the WriteFreely helper with `--sri`, run it again and restart
  WriteFreely:
  `python3 contrib/writefreely/add-comments.py /path/to/writefreely https://comments.example.com --sri`
- If you pasted the pinned snippet yourself, copy the new one from
  **Add to your blog** in the dashboard.

Without SRI there is nothing to do. Browsers may keep the old widget for up to
an hour (`Cache-Control: max-age=3600`). The old widget keeps working with the
new server in the meantime.

If you copied `afterword.css` into your blog's CSS instead of linking it from
the comment server, compare it with `afterword/static/afterword.css` and add
the new rules.

**6. Remove the backup from the volume**

Once you are satisfied, remove the copy inside the volume. Keep the one in
`~/afterword-backups` somewhere private for a while.

```sh
docker compose exec afterword rm /data/afterword-before-upgrade.db
```

### Rolling back (Docker Compose)

**Keep everything written since the upgrade (usually what you want).** Every
older version runs on a newer database as it is. It ignores the tables and
columns it does not know:

```sh
git checkout b284663        # 1.1.0   (1.0.0 is 6b9919e)
docker compose up -d --build
```

What the older version cannot show is simply missing while it runs. On 1.1,
replies appear as ordinary comments. On 1.0, there are no verified marks and
names are not reserved. Upgrade again (`git checkout master`, step 4) and
everything is back.

**Return to the exact state before the upgrade.** This loses every comment and
change made since the backup:

```sh
docker compose stop afterword
docker compose run --rm --no-deps --user root -v ~/afterword-backups:/restore:ro afterword sh -c \
  'cp /restore/afterword-before-upgrade.db /data/afterword.db &&
   rm -f /data/afterword.db-wal /data/afterword.db-shm &&
   chown afterword:afterword /data/afterword.db && chmod 600 /data/afterword.db'
git checkout b284663        # the version you came from
docker compose up -d --build
```

Removing the `-wal` and `-shm` files matters. Otherwise SQLite would try to
apply leftovers from the newer database to the restored file.

### Trying a new version on a copy first (optional)

You can run the new version next to your live container, on a copy of your
data, and try it on a local test page before touching the live service.

```sh
git clone https://github.com/LoredCast/afterword afterword-trial
cd afterword-trial
cp ~/afterword-backups/afterword-before-upgrade.db trial.db      # the backup from step 2
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
  sh -c 'cp /restore/trial.db /data/afterword.db && chown -R afterword:afterword /data && chmod 600 /data/afterword.db'
docker compose -f compose.trial.yaml up -d
```

Now try it out:

1. Sign in at <http://127.0.0.1:8081/admin> with your usual dashboard password.
   It is part of the copied database.
2. In **Settings**, add `http://127.0.0.1:8000` to **Blog address**. To try
   pseudonyms, also tick **Let readers keep a name as a verified pseudonym**.
   Save.
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
   - The comments are open and the form is collapsed behind **Write a comment**.
   - **Reply** to a comment, then reply to that reply. Both appear under the
     first comment, each starting with “@name · date”. Publish them in the
     trial dashboard first, unless your moderation mode does it already.
   - With pseudonyms on: old comments show **unverified**. Tick **Keep this
     name as my pseudonym** and post; the comment then shows **verified**. In
     a private window, the same name, or `NAME` in capitals, is refused until
     you **Restore a pseudonym from a backup key** there. The **Pseudonyms**
     page lists the name, and **Release** works.

If the server is remote, forward both ports first:
`ssh -L 8081:127.0.0.1:8081 -L 8000:127.0.0.1:8000 you@your-server`.

Clean up afterwards. The trial copy contains email addresses:

```sh
docker compose -f compose.trial.yaml down -v
rm trial.db
```

### Upgrade without Docker (systemd and a virtualenv)

```sh
sudo -u afterword /opt/afterword/venv/bin/afterword --data /var/lib/afterword \
    backup /var/lib/afterword/afterword-before-upgrade.db
cd /path/to/afterword && git pull
sudo /opt/afterword/venv/bin/pip install /path/to/afterword
sudo systemctl restart afterword
sudo journalctl -u afterword -n 20                 # "Afterword 1.2.0 listening on …"
```

Then carry on with step 5 above. To roll back, check out the older version,
install it the same way and restart. The database needs no change.

## Version notes

### 1.2: replies, a collapsed form, a plain "verified" mark

| | |
|---|---|
| **Replies** | Every comment gets a quiet **Reply** link. Replies are shown one level deep under their comment; a reply to a reply joins the same conversation and starts with “@name · date”, linked to the comment it answers. Replies are moderated like any comment. **On by default**; turn off under **Settings → The comment form → Let readers reply to comments**. |
| **Collapsed form** | Only the comments are open. The form waits behind a **Write a comment** button (or opens with **Reply**). To keep the old always-open form, add `data-form="open"` to the `data-afterword` element on your blog. |
| **"verified" as text** | Pseudonym comments now show a plain **verified** next to the name, instead of a boxed “✓ verified pseudonym”. While pseudonyms are on, names containing the word “verified” are refused, like check marks, so no name can imitate the mark. |
| **Database** | Schema 2 → 3: adds `parent_id`, `reply_to`, `reply_to_author` and `reply_to_created` columns to `comments`. Existing comments stay top-level. |
| **`widget.js`, `afterword.css`** | Changed: update a pinned SRI hash (step 5), and copy the new CSS rules if you copied the file. |
| **Renamed text attribute** | Only if you customised it: `data-text-error-name-check-mark` is now `data-text-error-name-marker`. |
| **Configuration** | No new environment variables; `compose.yaml` and `Dockerfile` unchanged. |

### 1.1: pseudonyms

| | |
|---|---|
| **New feature** | Optional [pseudonyms](docs/pseudonyms.md). **Off by default**: until you turn it on, readers see no difference. |
| **Turning it on** | **Settings → The comment form → Let readers keep a name as a verified pseudonym → Save settings**. Existing comments then show **unverified**, and the form offers **Keep this name as my pseudonym**. |
| **Database** | Schema 1 → 2: adds a `pseudonyms` table and a `pseudonym_id` column on `comments`. Existing comments are not touched; their new column is empty, so none of them can ever show as verified. |
| **`widget.js`, `afterword.css`** | Changed: update a pinned SRI hash (step 5), and copy the new CSS rules if you copied the file. |
| **Configuration** | No new environment variables; `compose.yaml`, `Dockerfile` and the systemd unit unchanged. |
