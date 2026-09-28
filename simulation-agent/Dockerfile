# Build only from the locally verified pinned baseline; builder checks its ID.
ARG BASE_IMAGE=track3-abides-baseline:agenthon-local-20260922
FROM ${BASE_IMAGE}
LABEL qfbench2.interface_version="2.0"
COPY patch_timestamp_cache.py /opt/participant_build/patch_timestamp_cache.py
RUN python /opt/participant_build/patch_timestamp_cache.py
# Preserve both official simulate entry points and all simulation behavior.
