FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace

# Runtime dependencies for the pinned source tree plus FastAPI's current test group.
# The repository itself is mounted at /workspace at run time; PYTHONPATH ensures the
# local source wins over any site-packages module.
RUN python -m pip install --no-cache-dir \
      "starlette>=0.46.0" \
      "pydantic>=2.9.0,<3.0.0" \
      "typing-extensions>=4.8.0" \
      "typing-inspection>=0.4.2" \
      "annotated-doc>=0.0.2" \
      "httpx>=0.27.0,<1.0.0" \
      "httpx2>=2.0.0" \
      "python-multipart>=0.0.18" \
      "email-validator>=2.0.0" \
      "jinja2>=3.1.5" \
      "itsdangerous>=1.1.0" \
      "pydantic-settings>=2.0.0" \
      "pydantic-extra-types>=2.0.0" \
      "anyio[trio]>=3.2.1,<5.0.0" \
      "coverage[toml]>=7.13,<8.0" \
      "dirty-equals>=0.9.0" \
      "flask>=3.0.0,<4.0.0" \
      "inline-snapshot>=0.21.1" \
      "mypy>=1.14.1" \
      "pwdlib[argon2]>=0.2.1" \
      "pyjwt>=2.9.0" \
      "pytest>=9.0.0" \
      "pytest-codspeed>=4.3.0" \
      "pyyaml>=5.3.1,<7.0.0" \
      "ruff>=0.14.14,<0.16.0" \
      "sqlmodel>=0.0.31" \
      "strawberry-graphql>=0.200.0,<1.0.0" \
      "ty>=0.0.25" \
      "typer>=0.26.1" \
      "a2wsgi>=1.9.0,<=2.0.0" \
      "pytest-xdist[psutil]>=3.0.2" \
      "pytest-cov>=4.0.0" \
      "pytest-sugar>=1.0.0" \
      "pytest-timeout>=2.4.0"

COPY container_entrypoint.py /container_entrypoint.py

WORKDIR /workspace
ENTRYPOINT ["python", "/container_entrypoint.py"]
