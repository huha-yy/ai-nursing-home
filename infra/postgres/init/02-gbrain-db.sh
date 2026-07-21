#!/bin/bash
# Postgres init script for GBrain (runs once at postgres container initialization).
# Creates the gbrain database and user.

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE gbrain;

    CREATE USER gbrain_app WITH PASSWORD '${DL_GBRAIN_PG_PASSWORD}';

    GRANT ALL PRIVILEGES ON DATABASE gbrain TO gbrain_app;

    \c gbrain

    CREATE EXTENSION IF NOT EXISTS vector;

    GRANT ALL ON SCHEMA public TO gbrain_app;
EOSQL
