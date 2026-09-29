FROM python:3.13-slim-bookworm
LABEL qfbench2.interface_version="2.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
RUN pip install --no-cache-dir \
    "numpy==2.1.3" "pandas==2.2.3" "pyarrow==18.1.0" "jsonschema==4.23.0" \
    "qfbench2-common @ https://github.com/Agenthon-2026/Agenthon2026-public/archive/refs/tags/v2.4.4.tar.gz#subdirectory=common"
WORKDIR /app
COPY forecast.py joint_model.py reference_model.py online_model.py text_events.py ./
COPY qfbench2_track_forecasting ./qfbench2_track_forecasting
COPY upstream-LICENSE /app/licenses/track2-LICENSE
USER 65534:65534
ENTRYPOINT ["python", "/app/forecast.py"]
