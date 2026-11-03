# Prediction service. It runs on Linux because scikit-learn wheels are not
# reliable on the Windows host, and it is built with the models inside: the
# training CSVs under data/ are copied in and `train.py` runs at build time, so
# the image carries exactly the models its metrics.json describes.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LightGBM and XGBoost link against OpenMP, which the slim image does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/ml

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The embedding model is downloaded NOW, at build time, into a fixed folder: at
# run time the container needs no internet (HF_HUB_OFFLINE) and the first /embed
# only has to read it from disk. This layer is cached until requirements change.
ENV HF_HOME=/opt/hf \
    EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL'])"
ENV HF_HUB_OFFLINE=1

COPY . .
RUN python train.py

# Not root: the service only needs to read the artifacts.
RUN useradd --create-home --uid 1000 ml && chown -R ml:ml /srv/ml /opt/hf
USER ml

# No port is published by docker-compose: only the API reaches this service,
# by name, on the internal network.
EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --retries=10 --start-period=15s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
