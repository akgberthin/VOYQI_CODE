#!/bin/bash
# Creates one database per service (comma-separated in
# POSTGRES_MULTIPLE_DATABASES) so local dev mirrors the database-per-service
# rule enforced in Cloud SQL by terraform/modules/cloudsql.
set -e

if [ -n "$POSTGRES_MULTIPLE_DATABASES" ]; then
  echo "Creating databases: $POSTGRES_MULTIPLE_DATABASES"
  IFS=',' read -ra DBS <<< "$POSTGRES_MULTIPLE_DATABASES"
  for db in "${DBS[@]}"; do
    db_trimmed=$(echo "$db" | xargs)
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
      SELECT 'CREATE DATABASE $db_trimmed'
      WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db_trimmed')\gexec
EOSQL
  done
fi
