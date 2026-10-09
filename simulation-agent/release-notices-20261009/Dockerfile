# The build driver must verify this local parent has image ID:
# sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9
FROM simulation-agent:unlocked-queue-scalar-dev-20260928
# Add redistribution notices only. No runtime source or image configuration changes.
COPY notices/ /usr/share/licenses/agenthon-track3/
RUN chmod 755 /usr/share/licenses /usr/share/licenses/agenthon-track3 \
    && chmod 644 /usr/share/licenses/agenthon-track3/*
