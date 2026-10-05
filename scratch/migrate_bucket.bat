@echo off

set BUCKET_NAME=esa-poc-2026-useast1

echo Creating bucket %BUCKET_NAME% in us-east-1...
aws s3 mb s3://%BUCKET_NAME% --region us-east-1

echo Updating lambda environment variables...
aws lambda update-function-configuration --function-name esa-ontology-ingest --environment "Variables={S3_BUCKET_NAME=%BUCKET_NAME%,AWS_EXECUTION_ENV=true,RDF_OUTPUT_KEY=ontology/centree/ontology.nq}"
aws lambda update-function-configuration --function-name esa-abox-generation --environment "Variables={S3_BUCKET_NAME=%BUCKET_NAME%,AWS_EXECUTION_ENV=true,STRUCTURED_DATA_PATH=/tmp/records,RDF_OUTPUT_KEY=knowledge/abox/instances.nq}"
aws lambda update-function-configuration --function-name esa-document-index --environment "Variables={S3_BUCKET_NAME=%BUCKET_NAME%,AWS_EXECUTION_ENV=true,EMBEDDING_PROVIDER=bedrock,LLM_PROVIDER=bedrock,DOCS_DIR=/tmp/documents}"
aws lambda update-function-configuration --function-name esa-graph-load --environment "Variables={S3_BUCKET_NAME=%BUCKET_NAME%,AWS_EXECUTION_ENV=true,RDF_OUTPUT_KEY=knowledge/abox/instances.nq,NEO4J_URI=YOUR_NEO4J_URI,NEO4J_USER=neo4j,NEO4J_PASSWORD=YOUR_NEO4J_PASSWORD,NEO4J_DATABASE=neo4j}"

echo Granting S3 invoke permissions to the new bucket...
aws lambda add-permission --function-name esa-ontology-ingest --principal s3.amazonaws.com --statement-id allow-s3-invoke-useast1 --action lambda:InvokeFunction --source-arn arn:aws:s3:::%BUCKET_NAME% --source-account 163120011782
aws lambda add-permission --function-name esa-abox-generation --principal s3.amazonaws.com --statement-id allow-s3-invoke-useast1 --action lambda:InvokeFunction --source-arn arn:aws:s3:::%BUCKET_NAME% --source-account 163120011782
aws lambda add-permission --function-name esa-document-index --principal s3.amazonaws.com --statement-id allow-s3-invoke-useast1 --action lambda:InvokeFunction --source-arn arn:aws:s3:::%BUCKET_NAME% --source-account 163120011782
aws lambda add-permission --function-name esa-graph-load --principal s3.amazonaws.com --statement-id allow-s3-invoke-useast1 --action lambda:InvokeFunction --source-arn arn:aws:s3:::%BUCKET_NAME% --source-account 163120011782

echo Applying notification.json to the new bucket...
aws s3api put-bucket-notification-configuration --bucket %BUCKET_NAME% --notification-configuration file://notification.json

echo Done!
