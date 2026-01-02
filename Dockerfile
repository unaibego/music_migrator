FROM public.ecr.aws/lambda/python:3.11

# Copia requirements e instala deps
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir


COPY src ./src
COPY lambda_handler.py .

# Handler: archivo.funcion
CMD ["lambda_handler.lambda_handler"]