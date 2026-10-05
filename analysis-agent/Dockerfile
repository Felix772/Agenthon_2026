FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.5.0#subdirectory=common"
WORKDIR /app/analysis-agent
COPY analysis_agent /app/analysis-agent/analysis_agent
COPY model_client.py /app/qfbench-agent/agent/model_client.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL qfbench2.interface_version="2.0"
USER 65534:65534
ENTRYPOINT ["python", "-m", "analysis_agent.cli"]
