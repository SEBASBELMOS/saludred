# Imagen de la API. Todas las dependencias quedan instaladas aqui: ningun
# script del proyecto instala paquetes ni descarga nada en tiempo de ejecucion.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias se copian e instalan antes que el codigo: esta capa solo se
# reconstruye cuando cambia requirements, de modo que editar codigo no obliga a
# reinstalar todo el stack.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# El proceso no corre como root: si alguien lograra ejecutar codigo dentro del
# contenedor, no tendria privilegios administrativos sobre el sistema de archivos.
RUN useradd --create-home --uid 1000 saludred \
    && chown -R saludred:saludred /app
USER saludred

EXPOSE 8000

# Se invoca con bash explicito para no depender del bit de ejecucion del
# archivo, que se pierde al clonar el repositorio en Windows.
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
