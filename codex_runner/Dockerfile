FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && npm install --global @openai/codex \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runner
COPY codex_runner/requirements.txt ./requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir --upgrade "pip>=26.2.1,<27" \
    && python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt
COPY codex_runner/runner.py ./runner.py

RUN useradd --create-home --uid 10002 runner \
    && mkdir -p /codex-home \
    && chown -R runner:runner /runner /codex-home
USER runner

ENV CODEX_HOME=/codex-home
EXPOSE 8090
CMD ["python3", "-m", "uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
