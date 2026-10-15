# Keycloak for PlantGuard (production): Postgres-backed, health/metrics enabled, pre-built ("start --optimized"
# starts in seconds, which Cloud Run needs). The realm file is mounted at runtime from Secret Manager.
FROM quay.io/keycloak/keycloak:26.0 AS builder
# build-time options (cache=local: one instance; Cloud Run has no cluster discovery)
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=true KC_CACHE=local
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.0
COPY --from=builder /opt/keycloak/ /opt/keycloak/
ENV KC_PROXY_HEADERS=xforwarded KC_HTTP_ENABLED=true KC_HTTP_PORT=8080 KC_HOSTNAME_STRICT=true
EXPOSE 8080
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
