# A throwaway image for running the GENERATED notebooks against a real local Spark + Delta.
# Not part of the app and not used by pytest: it exists because a generated notebook can only be
# trusted once it has actually executed, and Databricks isn't available from CI or a laptop.
#
#   docker build -t refinaire-spark-e2e saas/backend/tests/spark_e2e
#   docker run --rm -v <repo>:/repo:ro -v refinaire-ivy:/root/.ivy2 refinaire-spark-e2e \
#       python /repo/saas/backend/tests/spark_e2e/run_e2e.py
#
# Spark 4.0 + Delta 4.0: the same major versions as Databricks Runtime 17 (ANSI mode on by default).
# Built on the backend image so the engine's own dependencies (jinja2, pandas, ...) are already
# there. The Java runtime is copied from an image that ships one rather than installed with apt,
# which is slow and brittle on this base.
ARG BASE=saas-backend:latest

FROM eclipse-temurin:21-jre AS jre

FROM ${BASE}

COPY --from=jre /opt/java/openjdk /opt/java/openjdk
RUN pip install --no-cache-dir pyspark==4.0.0 delta-spark==4.0.0

ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    PYTHONPATH=/repo \
    PYSPARK_PYTHON=python \
    SPARK_LOCAL_IP=127.0.0.1
WORKDIR /work
