FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
ENV APP_HOME=/app
ENV PORT=8080
WORKDIR $APP_HOME
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers=2", "app:app"]
