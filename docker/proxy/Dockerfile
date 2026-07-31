FROM ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c

USER root
COPY --chown=13:13 docker/proxy/squid.conf /etc/squid/squid.conf
COPY --chown=13:13 docker/proxy/authorized-sources.conf /etc/squid/authorized-sources.conf
COPY --chown=13:13 docker/proxy/connector-ports.conf /etc/squid/connector-ports.conf
COPY --chmod=0555 docker/proxy/entrypoint.sh /usr/local/bin/nodease-egress-proxy

USER 13:13
EXPOSE 3128 3129 3130

ENTRYPOINT ["/usr/local/bin/nodease-egress-proxy"]
CMD ["-NYC", "-f", "/run/squid/squid.conf"]
