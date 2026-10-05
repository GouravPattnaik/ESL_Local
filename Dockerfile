FROM public.ecr.aws/lambda/python:3.11

# Copy requirements file
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# Upgrade pip and install dependencies using pre-compiled binary wheels
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copy source code
COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/

# Set a default command (this will be overridden in the AWS Lambda function configuration)
CMD [ "src.esa.lambda_handlers.ontology_handler" ]
