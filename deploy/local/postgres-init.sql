-- docker-compose only: the same databases as Cloud SQL (04-database.sh)
CREATE DATABASE plantguard_ot OWNER plantguard;
CREATE DATABASE plantguard_it OWNER plantguard;
CREATE USER keycloak WITH PASSWORD 'keycloak';
CREATE DATABASE keycloak OWNER keycloak;
