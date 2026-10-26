# Independent Hermes Agent on the NAS

Installed 2026-10-25 from the official Nous Research stable Docker image.
Verified CLI: Hermes Agent v0.22.5, upstream bf0203a7, Python 3.14.9.
The image is pinned by digest in compose.json and excluded from Watchtower.

## Layout

- Container: hermes-agent; network: hermes-independent_default.
- Live Compose: /volume1/docker/hermes-agent/compose.json.
- Private persistent data: /volume1/docker/hermes-agent/data -> /opt/data.
- Hermes UID/GID: 1034:100 (NAS user sbasadre); working directory: /opt/data/workspace.
- No published ports, host network, Docker socket or Tenazas mounts.
- Authorized photo share: /volume1/photo -> /mnt/photo, read/write.
- Memory cap: 4 GiB. CPU shares: 512, a relative priority, not a CPU cap.
- This NAS kernel does not support NanoCPU quotas or PIDs limits.
- Restart policy: unless-stopped. Docker/DSM must start after a NAS reboot.

This is a CLI instance. Its main command is sleep infinity so Hermes can be
invoked interactively on demand. The official s6 entrypoint is preserved.
No messaging channel, dashboard or recurring task has been configured.
OpenAI Codex authentication and a model response were confirmed by the user.
The user also verified creation and reading of /opt/data/workspace/prueba.txt.

## Configure and use

Run the provider picker in the NAS terminal:

```sh
sudo /usr/local/bin/docker exec -it -u hermes hermes-agent hermes model
```

Select your OpenAI authentication method and model. Enter credentials locally;
do not paste them into chat. No credentials from Tenazas were copied.

Start an interactive conversation:

```sh
sudo /usr/local/bin/docker exec -it -u hermes hermes-agent hermes chat
```

Memory, sessions and configuration persist in the private data mount. Keep
backups private and keep that directory outside this repository.

Reconcile the container from its saved configuration:

```sh
sudo /usr/local/bin/docker compose -f /volume1/docker/hermes-agent/compose.json up -d
```

Image updates are explicit: review the release and digest before changing Compose.
Preserve the data directory when replacing a container. Use image updates rather
than the source updater inside this Docker installation.

Official sources:
- https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5
- https://github.com/NousResearch/hermes-agent/blob/v0.22.5/docker-compose.yml
- https://hermes-agent.nousresearch.com/docs/getting-started/installation

## Photo access

The user authorized read/write access to the NAS photo share for organization.
The bind mount refuses to create a missing source directory. Hermes now maps to
NAS user sbasadre (1034:100), whose existing photo permissions were verified;
no photo ACLs or recursive photo permissions were changed. The official image
bootstrap migrated ownership of Hermes private state to the mapped UID/GID.

Before recreation, Compose and provider configuration were backed up privately
under /volume1/docker/hermes-agent/backups/before-photo-20261026T012846Z.
No credentials are stored in this repository. Container verification confirmed
preserved authentication/configuration and workspace, plus creation, reading,
renaming and deletion of a temporary check file under /mnt/photo. Existing
photos were not changed, and no organization job was started.

Reopen the Hermes CLI after recreation and refer to /mnt/photo/<folder>.
Choose the intended subfolder and organization rules before moving real photos.
