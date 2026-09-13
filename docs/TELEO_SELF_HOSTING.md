# Teleo self-hosting

Kinetra Resonance exports a static, self-contained data directory. It does not upload or host user content and does not require a Vetrabyte server, central API, cloud account or Teleo installation.

## SELF-HOSTING READY

Upload the complete directory configured by `TELEO_EXPORT_DIR`. Its default is `<PROJECT_ROOT>/var/teleo_publish/`:

```text
teleo_publish/
├── catalog.json
└── tracks/
    └── <track-uuid>/
        └── experience.json
```

The server must expose:

- `/catalog.json`
- `/tracks/<track-uuid>/experience.json`

Teleo is configured with the server root URL and resolves each relative `experienceUrl` from the catalog. The exported directory may be copied unchanged with `scp`, `rsync`, a hosting panel, NAS tooling or an object-storage uploader. Kinetra does not perform those transfers in this release.

## Server requirements

A compatible host needs only static file serving, HTTP `GET`, readable paths and the `application/json` MIME type for `.json`. HTTPS is recommended for development and expected for production. Directory listings should remain disabled. Native Teleo clients do not require browser CORS; configure CORS separately only for a future browser consumer that needs it.

Suitable infrastructure includes Nginx or Apache on a VPS, a NAS, institutional infrastructure, static hosting, or object storage/CDN. No Vetrabyte domain or fixed host is part of the protocol.

## Nginx example

Copy the bundle contents to `/srv/teleo-music/`, then use a generic virtual host such as:

```nginx
server {
    listen 80;
    server_name music.example.com;
    root /srv/teleo-music;
    autoindex off;

    location / {
        try_files $uri =404;
    }

    location ~ \.json$ {
        default_type application/json;
        try_files $uri =404;
    }
}
```

Terminate HTTPS according to the operator's infrastructure. Kinetra does not request or manage certificates.

## Protected hosting

The protocol works on public or protected hosts. An operator may place Bearer-token validation, reverse-proxy authentication or another gateway in front of the static directory if the chosen Teleo client supports it. Authentication is transport policy, not a required part of Teleo Music Protocol v1.

Do not place real tokens, passwords, SSH keys or server credentials in this repository or in the bundle. Kinetra stores no remote publication credentials.

## Export commands

Export a ProcessingJob UUID, or a Track UUID whose latest exportable job should be selected:

```bash
python manage.py export_teleo_track <job-or-track-uuid>
```

Use another local destination without changing the JSON:

```bash
python manage.py export_teleo_track <job-or-track-uuid> --output /srv/staging/teleo-music
```

Explicitly include existing canonical lyrics only when distribution is authorized:

```bash
python manage.py export_teleo_track <job-or-track-uuid> --include-lyrics
```

Rebuild a catalog from the publication records associated with a bundle:

```bash
python manage.py build_teleo_catalog
```

Export the latest canonical experience for every exportable track:

```bash
python manage.py export_teleo_library
```

The same `LocalBundlePublisher` service powers these commands and the **Export for Teleo** action on a track page.

## Operator responsibility

Kinetra Resonance does not host user content. Operators choose where generated files are stored and served, and remain responsible for their infrastructure, content permissions and licenses, and access-control policy.
