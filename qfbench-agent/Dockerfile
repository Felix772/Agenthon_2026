ARG BASE_IMAGE=finance-bench-sandbox:latest
FROM ${BASE_IMAGE}
LABEL qfbench2.interface_version="2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV HOME=/tmp XDG_CACHE_HOME=/tmp MPLCONFIGDIR=/tmp/matplotlib NUMBA_CACHE_DIR=/tmp/numba
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip freeze > /app/runtime-versions.txt
COPY agent ./agent
ENTRYPOINT ["python", "-m", "agent"]
CMD ["--help"]
