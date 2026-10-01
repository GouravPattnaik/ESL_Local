# ESA Pipeline — Sequential Step Guide

**Enterprise Semantic Analytics (ESA)** — Local POC to AWS Architecture Runbook

> This guide explains **what each script does**, **why it exists in the architecture**, **when to run it**, and the exact commands to use.

---

## Quick Reference

| Step | Script | What it does | Depends on |
|------|--------|--------------|------------|
| 1 | `step1_ontology_tbox.py` | Parse CENtree T-Box ontology to N-Quads | Nothing |
| 2 | `step2_metadata_normalisation.py` | Normalise raw records (field names, types) | Nothing |
| 3 | `step3_semantic_mapping.py` | Map normalised fields to ontology IRIs | Step 1 + 2 |
| 4 | `step4_entity_resolution.py` | Resolve canonical entity identities | Step 2 |
| 5 | `step5_abox_rdf_generation.py` | Build A-Box RDF + SHACL validation | Step 2, 3, 4 |
| 6 | `step6_document_processing.py` | Extract, chunk, embed documents to ChromaDB | Nothing |
| 7 | `step7_graph_load.py` | Load A-Box N-Quads to Neo4j / Neptune | Step 5 |
| 8 | `step8_query_rag.py` | Semantic search + RAG over documents | Step 6 |
| - | `run_pipeline.py` | **Run all steps 1-7 in one command** | Nothing |

---

## Architecture Overview

The architecture has three parallel pipelines that converge at Neptune:

```
PIPELINE 1: Ontology / T-Box  (Step 1)
  CENtree -> PyOxigraph -> N-Quads -> SHACL -> S3 -> Neptune (T-Box named graph)

PIPELINE 2: Enterprise Data / A-Box  (Steps 2 -> 3 -> 4 -> 5 -> 7)
  Databricks -> Collibra -> Metadata Norm -> Semantic Mapping -> Entity Resolution
  -> RDF Generation -> SHACL Validation -> S3 -> Neptune (A-Box instances)

PIPELINE 3: Document Processing  (Steps 6 -> 8)
  SharePoint/S3 -> Bedrock/local -> Vector Store -> Entity Resolution -> RDF
  -> Neptune (document triples)  +  RAG query interface
```

---

## Prerequisites

```powershell
# 1. Create and activate virtual environment
py -3.11 -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and configure .env
copy .env.example .env
# Edit .env - at minimum check these:
#   CENTREE_TTL_PATH=data/centree/ontology.ttl
#   STRUCTURED_DATA_PATH=data/records
#   DOCS_DIR=data/documents
#   EMBEDDING_PROVIDER=local   (or openai - requires OPENAI_API_KEY)
```

All commands below assume you are in the project root (`d:\Semantic_Layer`) with the venv activated.

---

## Run Everything at Once

```powershell
# Run all steps 1-6 (skips Neo4j graph load - safe if Neo4j is not running)
python scripts/run_pipeline.py --skip-graph-load

# Run all steps including Neo4j graph load
python scripts/run_pipeline.py

# Run with SHACL validation enabled in Step 5
python scripts/run_pipeline.py --skip-graph-load --validate-shacl

# Run only specific steps (e.g. re-run Steps 3, 4, 5 after changing data)
python scripts/run_pipeline.py --steps 3 4 5

# Step 7 in dry-run mode (show queries without Neo4j connection)
python scripts/run_pipeline.py --steps 7 --dry-run-graph
```

---

## Step-by-Step Guide

---

### Step 1 - Ontology / T-Box Ingestion

**Script:** `scripts/step1_ontology_tbox.py`
**When to run:** First, and whenever the CENtree ontology is updated or re-exported.

**What it does:**
- Reads the CENtree Turtle file (.ttl) - the T-Box vocabulary / schema.
- Parses it with PyOxigraph into an RDF store.
- Serialises it to N-Quads format (.nq) - the wire format for Amazon Neptune.
- Optionally validates the ontology with pySHACL.

**Why this step exists:**
The T-Box defines the vocabulary that all A-Box instance data must conform to.
It defines ex:Customer, ex:hasLoan, ex:creditScore, etc.
Neptune loads it as a named graph giving SPARQL queries their schema context.
In AWS: CENtree publishes a webhook -> API Gateway -> Lambda (ExportOntology) -> S3.

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| `data/centree/ontology.ttl` | CENtree API export (webhook-triggered) |
| PyOxigraph parse | Lambda: ExportOntology (PyOxigraph in container) |
| `local_bucket/ontology/centree/ontology.nq` | `s3://<bucket>/ontology/centree/ontology.nq` |
| Optional pySHACL | Lambda: SHACL Validation (pySHACL) |

```powershell
# Basic run
python scripts/step1_ontology_tbox.py

# With SHACL validation (requires: pip install pyshacl)
python scripts/step1_ontology_tbox.py --validate-shacl

# Use a different TTL file
python scripts/step1_ontology_tbox.py --ttl-path data/centree/my_ontology.ttl
```

**Output files:**
```
local_bucket/ontology/centree/ontology.nq
logs/step1_ontology_YYYYMMDD_HHMMSS.log
```

**Expected log output:**
```
08:00:01  INFO  step1.ontology_tbox - Parsed 38 RDF triples from ontology
08:00:01  INFO  step1.ontology_tbox - Step 1 COMPLETE
```

---

### Step 2 - Metadata Normalisation

**Script:** `scripts/step2_metadata_normalisation.py`
**When to run:** After Step 1, or independently whenever source data changes.

**What it does:**
- Loads raw JSON records (customers, accounts, loans) from `data/records/`.
- Standardises field names using a synonym map (simulates Collibra business glossary).
  Example: `cust_num` -> `customer_id`, `amt` -> `amount`, `bal` -> `balance`
- Coerces data types: IDs to str, amounts to float, credit scores to int.
- Validates required fields (data quality check like Collibra would enforce).
- Drops invalid rows and logs them as warnings.

**Why this step exists:**
Raw data from Databricks tables has inconsistent field names across source systems.
Collibra provides the business glossary - this step simulates its synonym resolution.
Semantic Mapping (Step 3) needs canonical field names to map to ontology IRIs correctly.
In AWS: Databricks -> Glue catalogue -> Lambda (Metadata Normalisation) -> S3.

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| `data/records/*.json` | Databricks tables / Glue Data Catalogue |
| Synonym map (CUSTOMER_FIELD_MAP etc.) | Collibra API (business glossary lookup) |
| `local_bucket/normalised/*.json` | `s3://<bucket>/normalised/` |

```powershell
python scripts/step2_metadata_normalisation.py
```

**Output files:**
```
local_bucket/normalised/customers.json
local_bucket/normalised/accounts.json
local_bucket/normalised/loans.json
logs/step2_metadata_norm_YYYYMMDD_HHMMSS.log
```

**Expected log output:**
```
08:00:02  INFO  step2.metadata_normalisation - [customers] Output: 10 valid records | 0 invalid
08:00:02  INFO  step2.metadata_normalisation - Step 2 COMPLETE
```

---

### Step 3 - Semantic Mapping

**Script:** `scripts/step3_semantic_mapping.py`
**When to run:** After Steps 1 and 2.

**What it does:**
- Scans all normalised records to discover unique field names.
- Applies a SEMANTIC_MAPPING_TABLE mapping each field to its ontology IRI and XSD datatype.
- Flags unmapped fields for human review.
- Writes an approved mapping report as JSON.

**Why this step exists:**
This is what makes the analytics *semantic* - it bridges raw business fields to the shared
ontology vocabulary. A-Box RDF generation reads this mapping report to create correct,
ontology-aligned triples. In AWS: Lambda (Semantic Mapping / LLM) uses Collibra metadata
plus LLM for candidate field mappings, then human review approves before A-Box generation.

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| SEMANTIC_MAPPING_TABLE (in script) | Lambda: Semantic Mapping (LLM + Collibra API) |
| Unmapped field warnings in log | Human review / Jira ticket |
| `local_bucket/semantic_mapping/approved_mappings.json` | `s3://<bucket>/semantic_mapping/approved_mappings.json` |

```powershell
python scripts/step3_semantic_mapping.py
```

**Output files:**
```
local_bucket/semantic_mapping/approved_mappings.json
logs/step3_semantic_mapping_YYYYMMDD_HHMMSS.log
```

**Expected log output:**
```
[Mapping]  customer_id   -> https://example.org/esa/customerId   [rule] (100%)
[Mapping]  credit_score  -> https://example.org/esa/creditScore  [rule] (100%)
Coverage: 100.0%
Step 3 COMPLETE
```

---

### Step 4 - Entity Resolution

**Script:** `scripts/step4_entity_resolution.py`
**When to run:** After Step 2 (uses normalised customers).

**What it does:**
- Loads normalised customer records (Step 2 output or raw fallback).
- Phase 1: Strong ID matching - `customer_id` is the canonical key.
- Phase 2: Fuzzy name scan (RapidFuzz token_sort_ratio) at configurable threshold (default 96%).
  High-similarity pairs flagged as manual_review - never auto-merged.
- Writes structured ER report: canonical_entities, decisions, review_candidates.

**Why this step exists:**
Multiple source systems may have the same customer under slightly different names -
for example "Ravi Kumar" vs "R. Kumar" (the exact example in the architecture diagram).
Without ER, A-Box RDF generation creates duplicate customer nodes in Neptune.
In AWS: Lambda (Entity Resolution / ML) using Databricks, Glue, or dedicated MDM service.

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| `local_bucket/normalised/customers.json` | S3 (Normalised Metadata) |
| `resolve_customers()` in entity_resolution.py | Lambda: Entity Resolution (ML) |
| review_candidates warnings in log | Human review queue / Jira |
| `local_bucket/knowledge/entity_resolution/results.json` | `s3://<bucket>/knowledge/entity_resolution/results.json` |

```powershell
# Default threshold (96%)
python scripts/step4_entity_resolution.py

# Lower threshold - flag more candidates for review
python scripts/step4_entity_resolution.py --threshold 90
```

**Output files:**
```
local_bucket/knowledge/entity_resolution/results.json
logs/step4_entity_resolution_YYYYMMDD_HHMMSS.log
```

> WARNING: If review candidates appear in the log, inspect results.json and confirm
> whether those records represent the same entity before proceeding to Step 5.

---

### Step 5 - A-Box RDF Generation + SHACL Validation

**Script:** `scripts/step5_abox_rdf_generation.py`
**When to run:** After Steps 2, 3, and 4 (or at minimum after Step 2).

**What it does:**
- Loads normalised records (Step 2) and approved semantic mapping (Step 3) for audit log.
- Builds A-Box RDF with PyOxigraph:
  - ex:Customer nodes with all properties + ex:registeredAt branch link.
  - ex:Account nodes + Customer -> ex:ownsAccount -> Account quads.
  - ex:Loan nodes + Customer -> ex:hasLoan -> Loan + Loan -> ex:securedBy -> Account quads.
- SHACL validation against data/shapes/ontology_shapes.ttl.
- Prints first 8 sample triples to console for spot-checking.
- Writes N-Quads to local_bucket/knowledge/abox/instances.nq.

**Why this step exists:**
N-Quads are the canonical serialisation format for Neptune RDF loading.
SHACL is the data quality gate - if the A-Box does not conform, loading to Neptune
should be blocked. In AWS: Lambda (RDF Generation) -> pySHACL -> S3 -> Neptune Bulk Load.

**Example triples generated:**
```
<https://example.org/esa/customer/C001> a <https://example.org/esa/Customer>
<https://example.org/esa/customer/C001> <https://example.org/esa/creditScore> "780"^^xsd:integer
<https://example.org/esa/customer/C001> <https://example.org/esa/hasLoan> <https://example.org/esa/loan/L001>
```

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| `build_abox()` in rdf.py | Lambda: RDF Generation (PyOxigraph) |
| `validate_shacl()` in rdf.py | Lambda: Data Validation (pySHACL) |
| `local_bucket/knowledge/abox/instances.nq` | `s3://<bucket>/knowledge/abox/instances.nq` |
| Neo4j load (Step 7) | Neptune Bulk Load via S3 |

```powershell
# Standard run (SHACL requires pyshacl: pip install pyshacl)
python scripts/step5_abox_rdf_generation.py

# Skip SHACL explicitly
python scripts/step5_abox_rdf_generation.py --skip-shacl
```

**Output files:**
```
local_bucket/knowledge/abox/instances.nq
logs/step5_abox_rdf_YYYYMMDD_HHMMSS.log
```

---

### Step 6 - Document Processing + Vector Store Indexing

**Script:** `scripts/step6_document_processing.py`
**When to run:** Any time, independent of Steps 1-5. Re-run when documents are added or updated.

**What it does:**
- Walks DOCS_DIR for .txt, .md, .pdf, .docx files.
- Extracts text with pypdf (PDFs) and python-docx (DOCX).
- Splits text into overlapping chunks (900 chars, 120-char overlap by default).
- Generates vector embeddings (local SentenceTransformers or OpenAI API).
- Upserts chunks and embeddings into a persistent ChromaDB collection.
- Runs a verification similarity search to confirm the index works.

**Why this step exists:**
Unstructured documents contain business knowledge not captured in structured tables.
Embedding them enables semantic search (RAG) - finding relevant passages by meaning,
not keyword matching. In AWS: Amazon Bedrock (extraction) -> Amazon OpenSearch (indexing).

**Local vs AWS mapping:**

| Local | AWS |
|-------|-----|
| `data/documents/` | Amazon S3 / SharePoint |
| SentenceTransformers embedding | Amazon Bedrock Titan Embeddings |
| ChromaDB at `local_bucket/vectorstore/chroma/` | Amazon OpenSearch Serverless |

```powershell
# Standard run
python scripts/step6_document_processing.py

# Custom verification query
python scripts/step6_document_processing.py --verify "What is the minimum credit score?"

# Custom chunk size
python scripts/step6_document_processing.py --chunk-chars 1200 --overlap 150 --top-k 5
```

> NOTE: First run downloads the SentenceTransformer model (about 90MB). Subsequent runs are fast.

**Output:**
```
local_bucket/vectorstore/chroma/
logs/step6_document_processing_YYYYMMDD_HHMMSS.log
```

---

### Step 7 - Graph Load (RDF to Neo4j / Neptune)

**Script:** `scripts/step7_graph_load.py`
**When to run:** After Step 5. Neo4j must be running (or use --dry-run).

**What it does:**
- Reads A-Box N-Quads from local_bucket/knowledge/abox/instances.nq.
- Loads all RDF statements into Neo4j as RDFTerm nodes + RDF_REL relationships.
- Prints sample Cypher queries (Neo4j Browser) and SPARQL queries (Neptune equivalent).
- --dry-run shows what would be loaded without connecting to Neo4j.

**Why this step exists:**
The knowledge graph enables complex traversal queries across
Customer -> Loan -> Account -> Branch relationships.
Neo4j is a local stand-in; in production use Neptune Bulk Load from S3.

```powershell
# Standard run (requires Neo4j running at bolt://localhost:7687)
python scripts/step7_graph_load.py

# Dry run - show triples without connecting
python scripts/step7_graph_load.py --dry-run

# Load a specific N-Quads file
python scripts/step7_graph_load.py --nq-path local_bucket/knowledge/abox/instances.nq
```

**Neo4j Browser queries (open http://localhost:7474):**

The loader creates **typed nodes** — `:Customer`, `:Account`, `:Loan`, `:Branch` — with real properties.

```cypher
// 1. Count all entity types
MATCH (n) RETURN labels(n)[0] AS type, count(n) AS total ORDER BY total DESC;

// 2. Customer profile — accounts, loans, branch
MATCH (c:Customer {id: 'C001'})
OPTIONAL MATCH (c)-[:OWNS_ACCOUNT]->(a:Account)
OPTIONAL MATCH (c)-[:HAS_LOAN]->(l:Loan)
OPTIONAL MATCH (c)-[:REGISTERED_AT]->(b:Branch)
RETURN c.fullName, c.creditScore,
       collect(DISTINCT a.accountType) AS accounts,
       collect(DISTINCT l.loanType) AS loans, b.branchId;

// 3. All customers ranked by credit score
MATCH (c:Customer)
RETURN c.id, c.fullName, c.creditScore
ORDER BY c.creditScore DESC;

// 4. Risky customers (score < 700) with loan amounts
MATCH (c:Customer)-[:HAS_LOAN]->(l:Loan)
WHERE c.creditScore < 700
RETURN c.fullName, c.creditScore, l.loanType, l.loanAmount
ORDER BY c.creditScore;

// 5. Branch-level loan exposure
MATCH (c:Customer)-[:REGISTERED_AT]->(b:Branch)
MATCH (c)-[:HAS_LOAN]->(l:Loan)
RETURN b.branchId, count(l) AS loans, sum(l.loanAmount) AS total_amount
ORDER BY total_amount DESC;

// 6. Visualise full graph (Neo4j Browser -- click the graph view tab)
MATCH path = (c:Customer)-[*1..2]->()
RETURN path LIMIT 50;
```

**Neptune SPARQL equivalent (AWS target):**
```sparql
PREFIX ex: <https://example.org/esa/>
SELECT ?name ?score WHERE {
  ?c a ex:Customer ; ex:fullName ?name ; ex:creditScore ?score .
} ORDER BY DESC(?score)
```

> NOTE: Neo4j is optional. Use --skip-graph-load in run_pipeline.py to skip.
> The N-Quads file at local_bucket/knowledge/abox/instances.nq is the canonical artifact.

---

### Step 8 - Query / RAG

**Script:** `scripts/step8_query_rag.py`
**When to run:** After Step 6 (ChromaDB must be populated). Interactive use at any time.

**What it does:**
- Accepts a natural-language question from the command line.
- Embeds it with the same model used in Step 6.
- Retrieves the top-K most semantically similar document chunks from ChromaDB.
- Displays retrieved passages with source file and chunk index.
- If LLM_PROVIDER=openai, generates a grounded LLM answer from retrieved context.

**Why this step exists:**
RAG prevents LLM hallucination - answers are constrained to retrieved document context.
In AWS: API Gateway -> Lambda (Query API) -> OpenSearch -> Bedrock.

```powershell
# Ask a question (retrieval only by default)
python scripts/step8_query_rag.py "What is the minimum credit score for a home loan?"

# Retrieve top 5 passages
python scripts/step8_query_rag.py --top-k 5 "Summarize the branch policy"

# With LLM answer (requires LLM_PROVIDER=openai + OPENAI_API_KEY in .env)
python scripts/step8_query_rag.py "What is the loan eligibility criteria?"
```

---

## Logs

All scripts write timestamped log files to `logs/`:

```
logs/
  step1_ontology_20260929_080001.log
  step2_metadata_norm_20260929_080002.log
  step3_semantic_mapping_20260929_080003.log
  step4_entity_resolution_20260929_080004.log
  step5_abox_rdf_20260929_080005.log
  step6_document_processing_20260929_080006.log
  step7_graph_load_20260929_080007.log
  step8_query_rag_20260929_080008.log
  run_pipeline_20260929_080000.log   <- master orchestrator log
```

Log entry format: `HH:MM:SS  LEVEL  module - message`

Each step log contains:
1. **PURPOSE** - why this step exists in the architecture
2. **INPUT** - what files/config are being read and from where
3. **PROCESSING** - what transformations are happening and why
4. **OUTPUT** - what files are written, triple counts, chunk counts, etc.
5. **NEXT STEP** - what script to run next

---

## Outputs Summary

```
local_bucket/
  ontology/centree/ontology.nq              <- Step 1: T-Box N-Quads
  normalised/customers.json                 <- Step 2: Normalised customers
  normalised/accounts.json                  <- Step 2: Normalised accounts
  normalised/loans.json                     <- Step 2: Normalised loans
  semantic_mapping/approved_mappings.json   <- Step 3: Field to IRI mappings
  knowledge/entity_resolution/results.json  <- Step 4: ER decisions + candidates
  knowledge/abox/instances.nq              <- Step 5: A-Box N-Quads
  vectorstore/chroma/                       <- Step 6: ChromaDB embeddings
```

---

## Architecture Gap Analysis

Which architecture diagram components are now covered:

| Architecture Component | Covered By | Status |
|---|---|---|
| CENtree Ontology / T-Box ingestion | step1_ontology_tbox.py | Covered |
| SHACL Validation | step1 (--validate-shacl) + step5 | Covered |
| Metadata Extraction (Databricks / Collibra) | step2_metadata_normalisation.py | Covered |
| Metadata Normalisation (Lambda) | step2_metadata_normalisation.py | Covered |
| Semantic Mapping (Lambda / LLM) | step3_semantic_mapping.py | Covered |
| Entity Resolution (Lambda / ML) | step4_entity_resolution.py | Covered |
| A-Box RDF Generation (PyOxigraph) | step5_abox_rdf_generation.py | Covered |
| Data Validation (pySHACL) | step5_abox_rdf_generation.py | Covered |
| Doc Processing (Bedrock / local) | step6_document_processing.py | Covered |
| Vector Store (OpenSearch / ChromaDB) | step6_document_processing.py | Covered |
| Graph Load (Neptune / Neo4j) | step7_graph_load.py | Covered |
| Query / RAG interface | step8_query_rag.py | Covered |
| Step Functions orchestration | run_pipeline.py (local subprocess) | Covered locally |
| Alert and Feedback | Log warnings + review_candidates output | Covered via logs |

**Previously missing components (now added as new scripts):**
- Step 2: Metadata Normalisation - was not a standalone runnable script
- Step 3: Semantic Mapping - was completely absent from the codebase
- SHACL validation - existed as helper in rdf.py but was never wired into any pipeline call
- Structured INPUT/OUTPUT/WHY logging across all steps - was minimal or absent

---

## Configuration Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `CENTREE_TTL_PATH` | `data/centree/ontology.ttl` | T-Box ontology input file |
| `SHACL_SHAPES_PATH` | `data/shapes/ontology_shapes.ttl` | SHACL shapes for validation |
| `STRUCTURED_DATA_PATH` | `data/records` | Raw JSON records directory |
| `DOCS_DIR` | `data/documents` | Unstructured documents directory |
| `LOCAL_BUCKET_DIR` | `local_bucket` | Output root (mirrors S3 key layout) |
| `RDF_OUTPUT_KEY` | `knowledge/abox/instances.nq` | A-Box N-Quads output key |
| `EMBEDDING_PROVIDER` | `local` | local (free) or openai (paid API) |
| `LOCAL_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model |
| `OPENAI_API_KEY` | (empty) | OpenAI API key - do not commit to git |
| `LLM_PROVIDER` | `none` | none (retrieval only) or openai (LLM answer) |
| `CHROMA_DIR` | `local_bucket/vectorstore/chroma` | ChromaDB storage path |
| `CHROMA_COLLECTION` | `esa_documents` | ChromaDB collection name |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI (optional) |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | (set in .env) | Neo4j password - do not commit |

---

## Troubleshooting

**ModuleNotFoundError: No module named 'esa'**
Run from the project root: `cd d:\Semantic_Layer` then run the script.

**FileNotFoundError: Ontology file not found**
Check `CENTREE_TTL_PATH` in .env. Verify with `dir data\centree\`.

**First run of Step 6 is very slow**
SentenceTransformer model downloads on first use (about 90MB). Wait; subsequent runs are fast.

**SHACL validation failed**
Inspect the report in the log. Likely a Customer is missing customerId or fullName.
Check `local_bucket/normalised/customers.json` for missing required fields.

**Neo4j connection refused**
Neo4j is optional. Use `--skip-graph-load` or `--dry-run` flags.
The N-Quads file is the canonical output - Neo4j is only for local visual exploration.

**OpenAI authentication error**
Check `OPENAI_API_KEY` in .env is valid and the model is accessible.

---

## Next Steps (Moving to AWS)

1. Replace `data/centree/ontology.ttl` with a live CENtree API export.
2. Configure S3 bucket and uncomment `write_s3` / `read_s3` in `src/esa/storage.py`.
3. Package each step as a separate Lambda function (see `src/esa/lambda_handlers.py`).
4. Configure Amazon OpenSearch instead of ChromaDB in `index_chunks()`.
5. Set up Neptune cluster and use S3 bulk loader for N-Quads.
6. Add AWS Step Functions state machine to orchestrate the full pipeline.
7. Configure CloudWatch alarms on Lambda errors and SHACL validation failures.
8. See `docs/AWS_DEPLOYMENT.md` for the full deployment checklist.
