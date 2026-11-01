# BetterHoster

A Minecraft server dashboard for managing Java and Bedrock servers.

## Run with Docker

Install Docker with Compose. On Windows, use Docker Desktop with Linux containers. The included image targets x86-64 Linux, and the Windows package runs it through Docker Desktop.

From this folder run:

```sh
docker compose up -d --build
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) and register the admin account. The Windows and Linux build archives also include a `start-windows.cmd` or `start-linux.sh` launcher.

The Compose setup includes MariaDB, Java 25 and 17, and OpenVSCode Server. Database records, Minecraft servers, and editor profiles are stored in Docker volumes. To stop the app without deleting the data, run `docker compose down`.

The default published Minecraft ports are TCP 25565–25575 and UDP 19132–19142. Configure a server to use a port in the relevant range. To change the published ranges, set `BETTERHOSTER_JAVA_PORTS` and `BETTERHOSTER_BEDROCK_PORTS` in the environment before starting Compose. Set `BETTERHOSTER_HTTP_PORT` to change the local dashboard port and `BETTERHOSTER_SERVER_ADDRESS` to the host's LAN address if you want it displayed in the dashboard. The editor uses local TCP ports 8100–8119.

`BETTERHOSTER_DB_PASSWORD` can override the internal database password before the first startup. If changed later, the stored MariaDB credentials must also be updated.

GitHub Actions builds the Docker image and produces Windows and Linux Docker-based distribution archives. These archives require Docker; they are not standalone native executables.
