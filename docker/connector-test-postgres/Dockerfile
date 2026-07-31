FROM pgvector/pgvector:pg15

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/connector-test-postgres/generate-certificates.sh \
    /usr/local/bin/generate-connector-test-certificates
COPY docker/connector-test-postgres/init-demo-role.sh \
    /docker-entrypoint-initdb.d/10-create-connector-demo-role.sh
COPY docker/connector-test-postgres/pg_hba.conf \
    /etc/postgresql/connector-demo-pg_hba.conf

RUN chmod 0755 \
        /usr/local/bin/generate-connector-test-certificates \
        /docker-entrypoint-initdb.d/10-create-connector-demo-role.sh \
    && chmod 0644 /etc/postgresql/connector-demo-pg_hba.conf
