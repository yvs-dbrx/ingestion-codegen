## Run it yourself

Docker Desktop must be running first (whale icon in the system tray). If a terminal says docker isn't recognized, close it and open a new one — terminals opened before the install don't see it.

Go to the folder:
cd C:\Sree\PyProject\ingestion-codegen\saas
Start everything. The first run takes a minute or two to set up:
docker compose up -d
Check that both show as running, with Postgres marked healthy:
docker compose ps
Open http://localhost:8080 and log in with:
System: PostgreSQL
Server: postgres
Username: ingestion_saas
Password: dev_password_change_me
Database: ingestion_saas

You should see the 8 tables.

Or list the tables from the command line:
docker compose exec postgres psql -U ingestion_saas -d ingestion_saas -c "\dt"

## Day to day:

docker compose down stops it and keeps your data.
docker compose down -v stops it and wipes the data. Use this after editing the schema script, since the script only runs on a fresh, empty database.
If port 5432 is already taken, copy .env.example to .env and change POSTGRES_PORT.

Docker basics, in case they help:

An image is a downloaded blueprint, like postgres:16.
A container is a running copy of one.
A volume is where a container's data survives restarts, here pgdata.
The compose file describes the whole setup, so one command starts everything.

## Run it

From saas/:

docker compose up -d --build

The first build takes a minute or two. Your existing database and its data stay as they are.

Then open http://localhost:8000/docs:

Run POST /auth/register with "Try it out".
Click Authorize at the top right. The username is your email.
Try GET /me and POST /sources.

To run the tests yourself:

docker compose exec backend pytest

The tests use a separate ingestion_saas_test database and refuse to touch anything else.