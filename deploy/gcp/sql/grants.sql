-- Least-privilege grants (run as the "postgres" admin after 04-database.sh).
-- plantguard owns its two databases (it creates and migrates the schema); keycloak owns only its own.
-- Nobody else can connect to them.
REVOKE ALL ON DATABASE plantguard_ot FROM PUBLIC;
REVOKE ALL ON DATABASE plantguard_it FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
ALTER DATABASE plantguard_ot OWNER TO plantguard;
ALTER DATABASE plantguard_it OWNER TO plantguard;
ALTER DATABASE keycloak OWNER TO keycloak;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantguard_ot TO plantguard;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE plantguard_it TO plantguard;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE keycloak TO keycloak;
\connect plantguard_ot
ALTER SCHEMA public OWNER TO plantguard;
\connect plantguard_it
ALTER SCHEMA public OWNER TO plantguard;
\connect keycloak
ALTER SCHEMA public OWNER TO keycloak;
