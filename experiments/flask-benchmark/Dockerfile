FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/src

RUN python -m pip install --no-cache-dir \
      "blinker>=1.9.0" \
      "click>=8.1.3" \
      "itsdangerous>=2.2.0" \
      "jinja2>=3.1.2" \
      "markupsafe>=2.1.1" \
      "werkzeug>=3.1.0" \
      asgiref \
      python-dotenv \
      pytest

COPY container_entrypoint.py /container_entrypoint.py

WORKDIR /workspace
ENTRYPOINT ["python", "/container_entrypoint.py"]
