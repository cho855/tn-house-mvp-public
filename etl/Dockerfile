FROM python:3.11-slim


WORKDIR /app


RUN pip install --no-cache-dir requests pandas psycopg[binary]


COPY . /app


CMD ["python", "run_etl.py"]
