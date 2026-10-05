@echo off

echo Creating esa-ontology-ingest...
aws lambda create-function ^
  --function-name esa-ontology-ingest ^
  --package-type Image ^
  --role arn:aws:iam::163120011782:role/EsaLambdaExecutionRole ^
  --code ImageUri=163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.ontology_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=esa-poc-2026,AWS_EXECUTION_ENV=true,RDF_OUTPUT_KEY=ontology/centree/ontology.nq}"

echo Creating esa-abox-generation...
aws lambda create-function ^
  --function-name esa-abox-generation ^
  --package-type Image ^
  --role arn:aws:iam::163120011782:role/EsaLambdaExecutionRole ^
  --code ImageUri=163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.abox_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=esa-poc-2026,AWS_EXECUTION_ENV=true,STRUCTURED_DATA_PATH=/tmp/records,RDF_OUTPUT_KEY=knowledge/abox/instances.nq}"

echo Creating esa-document-index...
aws lambda create-function ^
  --function-name esa-document-index ^
  --package-type Image ^
  --role arn:aws:iam::163120011782:role/EsaLambdaExecutionRole ^
  --code ImageUri=163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.document_index_handler\"]" ^
  --timeout 300 ^
  --memory-size 2048 ^
  --environment "Variables={S3_BUCKET_NAME=esa-poc-2026,AWS_EXECUTION_ENV=true,EMBEDDING_PROVIDER=bedrock,LLM_PROVIDER=bedrock,DOCS_DIR=/tmp/documents}"

echo Creating esa-graph-load...
aws lambda create-function ^
  --function-name esa-graph-load ^
  --package-type Image ^
  --role arn:aws:iam::163120011782:role/EsaLambdaExecutionRole ^
  --code ImageUri=163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest ^
  --image-config Command="[\"src.esa.lambda_handlers.graph_load_handler\"]" ^
  --timeout 300 ^
  --memory-size 1024 ^
  --environment "Variables={S3_BUCKET_NAME=esa-poc-2026,AWS_EXECUTION_ENV=true,RDF_OUTPUT_KEY=knowledge/abox/instances.nq,NEO4J_URI=YOUR_NEO4J_URI,NEO4J_USER=neo4j,NEO4J_PASSWORD=YOUR_NEO4J_PASSWORD,NEO4J_DATABASE=neo4j}"

echo Done.
