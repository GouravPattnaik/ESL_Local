@echo off
echo Rebuilding Docker image...
docker build --provenance=false -t esa-pipeline-repo .

echo Tagging and Pushing image...
docker tag esa-pipeline-repo:latest 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
docker push 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest

echo Updating Lambda functions...
aws lambda update-function-code --function-name esa-ontology-ingest --image-uri 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-abox-generation --image-uri 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-document-index --image-uri 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest
aws lambda update-function-code --function-name esa-graph-load --image-uri 163120011782.dkr.ecr.us-east-1.amazonaws.com/esa-pipeline-repo:latest

echo Done!
