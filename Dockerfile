FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY matchmind ./matchmind
COPY web ./web
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "matchmind.server:app", "--host", "0.0.0.0", "--port", "8000"]
