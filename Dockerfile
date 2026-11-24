FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
COPY ocr-ml/requirements.txt ./ocr-ml/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r ocr-ml/requirements.txt

COPY app ./app
COPY config ./config
COPY ocr-ml ./ocr-ml
COPY templates ./templates

RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home appuser \
    && mkdir -p data ocr-ml/data/feedback_loop/images ocr-ml/data/feedback_loop/labels \
    && chown -R appuser:appgroup /srv/app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "app"]
