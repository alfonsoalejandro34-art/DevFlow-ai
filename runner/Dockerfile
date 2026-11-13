# DevFlow sandbox image: runs untrusted pytest suites.
# Build: docker build -t devflow-runner:0.1 runner/
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: many real test suites drive it (temporary repos, diffs). With --network none it
# cannot fetch or push anything.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Identity for tests that create commits; a developer machine provides one via
# ~/.gitconfig, which the sandbox (HOME=/tmp, read-only root) does not have.
ENV GIT_AUTHOR_NAME="DevFlow Sandbox" GIT_AUTHOR_EMAIL="sandbox@devflow.invalid" \
    GIT_COMMITTER_NAME="DevFlow Sandbox" GIT_COMMITTER_EMAIL="sandbox@devflow.invalid"

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin sandbox

# DevFlow also passes --user 10001:10001; this is the default for direct `docker run`.
USER 10001:10001
WORKDIR /src
CMD ["python", "-m", "pytest", "--version"]
