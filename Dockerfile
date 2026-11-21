# Mech Royale probe P1 "hide" (pacifist hider, see probe_bot.py). bot.py is the unchanged v4.1 base class;
# probe_bot.py subclasses it and is the entry point. PROBE selects the mode (hide|kill1|open|idle).
FROM python:3.13.1-slim-bookworm@sha256:031ebf3cde9f3719d2db385233bcb18df5162038e9cda20e64e08f49f4b47a2f
WORKDIR /bot
COPY mechfight.py world.py bot.py probe_bot.py ./
RUN python3 -m compileall -q /bot && chmod -R a+rX /bot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PROBE=hide
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/bot/probe_bot.py"]
