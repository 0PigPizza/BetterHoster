# BetterHoster admin registration setup

This machine has MariaDB installed and configured for this project. The local `.env` file contains the database credentials; it is ignored by Git. The server loads it automatically, with environment variables taking precedence.

Start the registration server from the project directory:

```sh
.venv/bin/python server.py
```

Open http://127.0.0.1:8000/.

Registration stores an Argon2 password hash in MariaDB and allows one admin. The dashboard server listens on localhost. The Spigot installer requires Git and a Java development kit compatible with the selected Minecraft version.

For another installation, install and start MariaDB, create a database and a database user with SELECT and INSERT permissions, and run `schema.sql` in that database. Install Python dependencies with `python -m pip install -r requirements.txt`. Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` in the environment or a private `.env` file.
