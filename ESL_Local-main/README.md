# Enterprise Semantic Analytics (ESA) — End-to-End Local POC

A local-first proof of concept for an Enterprise Semantic Analytics architecture: ontology ingestion, structured-data mapping, RDF A-Box generation, document extraction, vector retrieval, entity-resolution candidates, and optional graph visualization.

> **Important:** This is a learning/POC scaffold, not a production-certified application. Sample data and ontology are fictional. Replace them with approved CENtree exports and authorized data. The AWS templates are guidance and require environment-specific implementation and testing.

---

## Contents

- [1. Architecture overview](#1-architecture-overview)
- [2. Local and AWS technology mapping](#2-local-and-aws-technology-mapping)
- [3. Key semantic concepts](#3-key-semantic-concepts)
- [4. Repository structure](#4-repository-structure)
- [5. Prerequisites](#5-prerequisites)
- [6. Install and run locally](#6-install-and-run-locally)
- [7. Pipeline walkthrough](#7-pipeline-walkthrough)
- [8. Document retrieval and OpenAI](#8-document-retrieval-and-openai)
- [9. Entity resolution](#9-entity-resolution)
- [10. Neo4j graph view](#10-neo4j-graph-view)
- [11. AWS deployment plan](#11-aws-deployment-plan)
- [12. Lambda design](#12-lambda-design)
- [13. Configuration reference](#13-configuration-reference)
- [14. Troubleshooting](#14-troubleshooting)
- [15. Security, privacy, and cost](#15-security-privacy-and-cost)
- [16. Limitations and next steps](#16-limitations-and-next-steps)

---

## 1. Architecture overview

### Local POC flow

```text
                  ┌───────────────────────────────┐
                  │ CENtree ontology export (.ttl) │
                  └──────────────┬────────────────┘
                                 v
                       PyOxigraph parse/serialize
                                 v
                     local_bucket/ontology/*.nq
                                 |
                                 |  T-Box vocabulary
                                 |
Structured records              |       Documents (PDF/TXT/MD/DOCX)
        |                        |                    |
        v                        |                    v
Source mapping + validation     |              Text extraction
        |                        |                    |
        v                        |               Chunking
Entity-resolution candidates    |                    |
        |                        |                    v
        v                        |             Embedding generation
A-Box RDF generation            |             (local or OpenAI)
        |                        |                    |
        v                        |                    v
SHACL validation (optional)     |              ChromaDB index
        |                        |                    |
        v                        |                    v
local_bucket/knowledge/*.nq     |               Retrieval / Q&A
        |                        |                    |
        └───────────────┬────────┘                    |
                        v                             |
              RDF artifacts remain canonical          |
                        |                             |
                        v                             |
                 Optional Neo4j graph view <──────────┘ (not automatic)
```

### AWS-oriented target

```text
CENtree export / source systems / document repository
        |
        v
S3 raw zone -> Lambda ingestion -> parse / map / validate
        |                                  |
        |                                  v
        |                           S3 processed RDF
        |                                  |
        v                                  v
Document extraction -> embeddings -> OpenSearch vector index
        |
        v
Entity resolution -> A-Box RDF -> SHACL validation -> S3
                                                |
                                                v
                                     Neptune RDF/SPARQL graph
```

The precise orchestration can use separate Lambda functions, Step Functions, EventBridge, or source-system events. The appropriate choice depends on data size, latency, and service limits.

---

## 2. Local and AWS technology mapping

| Capability | Local POC | AWS-oriented option |
|---|---|---|
| Object storage | `local_bucket/` folder | Amazon S3 |
| Compute | Python scripts | AWS Lambda; Step Functions for orchestration |
| Ontology/RDF | PyOxigraph | PyOxigraph packaged for Lambda/container |
| SHACL | pySHACL (optional) | pySHACL in Lambda/container |
| Structured source | Sample JSON file | Databricks, Snowflake, database/API, or S3 exports |
| Document parsing | pypdf, python-docx, text readers | Lambda/container or batch compute |
| Embeddings | SentenceTransformers locally or OpenAI API | Amazon Bedrock or approved OpenAI API |
| Vector store | ChromaDB | Amazon OpenSearch Service/Serverless (managed and billed) |
| Entity resolution | ID rules + RapidFuzz candidates | Lambda, Glue, Databricks, or dedicated service |
| RDF graph | N-Quads files | Amazon Neptune RDF/SPARQL |
| Optional property graph | Neo4j Community | Self-managed/Aura Neo4j as a separate choice |
| Logs | Console output | CloudWatch Logs, metrics, alarms |

**Do not treat these as exact drop-in replacements:** ChromaDB is not OpenSearch; Neo4j is not Neptune's RDF/SPARQL interface; OpenAI is a model/API provider, not a vector database; Bedrock is not a vector database either.

---

## 3. Key semantic concepts

### T-Box and A-Box

- **T-Box:** ontology vocabulary—classes, properties, and relationships. In this project, the sample Turtle file is the T-Box input.
- **A-Box:** instance data—specific customers, accounts, loans, and links represented as RDF statements.

The example ontology uses the illustrative namespace `https://example.org/esa/`. It is not an official CENtree namespace.

### Semantic mapping

Semantic mapping connects source-system fields to ontology concepts. For example, a source field `cust_num` might map to the ontology property `customerId`. Mapping requires agreed business meaning; changing column names alone is not semantic mapping.

### Entity resolution (ER)

ER determines whether multiple records or mentions refer to the same real-world entity. Strong identifiers and verified rules should drive merges. Fuzzy name similarity is only a candidate-generation signal. Ambiguous candidates should be reviewed. Use `owl:sameAs` only when true identity equivalence is established.

### SHACL

SHACL validates RDF data against a SHACL shapes graph. An OWL ontology is not automatically a SHACL shapes graph. The repository includes a small example shape; adjust its constraints to match your actual data contract.

### RDF artifacts and graph databases

The `.nq` files are RDF N-Quads artifacts. Keep them as canonical, versioned outputs. The Neo4j helper creates a simplified property-graph view; it does not preserve all RDF semantics or provide Neptune-compatible SPARQL behavior.

---

## 4. Repository structure

```text
esa_full_starter/
├── README.md
├── requirements.txt
├── .env.example
├── data/
│   ├── centree/ontology.ttl          # illustrative ontology; replace with approved export
│   ├── shapes/ontology_shapes.ttl    # sample SHACL shape
│   ├── records/customers.json        # fictional structured records
│   └── documents/customer_policy.txt # fictional text document
├── docs/
│   └── AWS_DEPLOYMENT.md             # deployment checklist
├── scripts/
│   ├── run_all.py                    # runs ontology, A-Box, document indexing
│   ├── index_documents.py            # indexes local documents
│   ├── ask_documents.py              # retrieval / optional LLM answer
│   └── load_neo4j.py                 # optional graph-view loader
└── src/esa/
    ├── config.py                     # .env configuration
    ├── storage.py                    # local storage + commented S3 examples
    ├── rdf.py                        # T-Box parsing, A-Box RDF, SHACL helper
    ├── documents.py                  # extraction and chunking
    ├── vectorstore.py                # Chroma + embeddings + optional OpenAI chat
    ├── entity_resolution.py          # ID-based identity + review candidates
    ├── pipeline.py                   # pipeline orchestration
    ├── graph_neo4j.py                # optional simplified graph adapter
    └── lambda_handlers.py             # thin handler examples
```

Generated outputs appear in `local_bucket/` and are not source files.

---

## 5. Prerequisites

- Windows, macOS, or Linux.
- Python 3.11 recommended.
- Internet access for installing dependencies and downloading the local embedding model on first use.
- Optional: OpenAI API key if you choose paid API embeddings or chat.
- Optional: local Neo4j Community database if you want the graph-view demo.

Some ML dependencies are sizable and may take time to install. Local embeddings avoid per-request API charges, but require local compute and a model download.

---

## 6. Install and run locally

### Windows CMD

From the extracted repository root:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Then run the main pipeline:

```bat
python scripts\run_all.py
```

Run the document retrieval demo:

```bat
python scripts\index_documents.py
python scripts\ask_documents.py "What does the sample policy say about customer identifiers?"
```

If the `py` command is unavailable, install Python 3.11 and enable the option to add Python to PATH, then reopen CMD. If `pip` is unavailable, use `python -m pip`.

### macOS/Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python scripts/run_all.py
```

### Expected outputs

- `local_bucket/ontology/centree/ontology.nq`: serialized T-Box RDF.
- `local_bucket/knowledge/abox/instances.nq`: sample A-Box RDF.
- `local_bucket/knowledge/entity_resolution/results.json`: identity decisions and review candidates.
- `local_bucket/vectorstore/chroma/`: persistent ChromaDB files.

Exact triple/chunk counts depend on the supplied inputs and code version.

---

## 7. Pipeline walkthrough

### Step A — Ontology ingestion

`run_ontology()` reads the configured Turtle file, parses it with PyOxigraph, serializes it as N-Quads, and writes it to the local bucket folder.

Replace `data/centree/ontology.ttl` with a valid, authorized CENtree export or change `CENTREE_TTL_PATH`.

This scaffold does not know your CENtree API URL, authentication, export format, or network requirements. Those must be supplied before live ingestion can be implemented.

### Step B — Structured records to A-Box RDF

`run_abox()` reads the sample JSON file, applies the simple example mapping, builds RDF for customers and their accounts/loans, and writes N-Quads plus an ER report.

This is a demonstration mapping, not a general schema mapper. For real sources, define field-to-IRI mappings, data type conversions, null rules, provenance, and stable URI conventions.

### Step C — SHACL validation

The `validate_shacl()` helper is available in `src/esa/rdf.py`. It is not automatically run by `run_all.py`. Integrate it after A-Box generation and before publishing RDF artifacts, once the shapes reflect your agreed constraints.

### Step D — Document extraction and indexing

`load_chunks()` supports `.txt`, `.md`, `.pdf`, and `.docx`. It extracts text and divides it into overlapping chunks. `index_chunks()` embeds chunks and upserts them into a persistent Chroma collection.

Scanned PDFs may require OCR, which is not included. Chunking and metadata should be tuned for your document types and retrieval use case.

### Step E — Retrieval and answer generation

`search_chunks()` retrieves similar chunks from Chroma. `answer_query()` returns retrieved passages by default. If `LLM_PROVIDER=openai`, it sends the retrieved context to the configured OpenAI chat model to generate a context-grounded response.

Retrieval quality depends on source quality, chunking, embedding model, metadata filters, and evaluation. Similarity is not proof of truth or entity identity.

---

## 8. Document retrieval and OpenAI

### Local mode (default)

The default `.env.example` uses `EMBEDDING_PROVIDER=local`, which uses SentenceTransformers. The model downloads on first use. `LLM_PROVIDER=none` means the script returns retrieved passages rather than calling an LLM.

### OpenAI mode (paid API)

1. Put your API key in `.env` as `OPENAI_API_KEY=...`.
2. Set `EMBEDDING_PROVIDER=openai` to use OpenAI embeddings.
3. Set `LLM_PROVIDER=openai` to enable generated answers.
4. Keep the key private and do not commit `.env`.

The OpenAI API is billed separately. Check current pricing and organization policies. Sending documents to an external model provider requires approval for the data involved.

---

## 9. Entity resolution

`resolve_customers()` uses a customer ID as the example stable key. It also reports high-similarity names as **manual-review candidates**; it does not automatically merge records based on names.

For a real ER workflow, define:
- authoritative identifiers and source-system precedence;
- normalization rules for names, addresses, dates, and identifiers;
- match/possible-match/no-match thresholds;
- provenance and audit history;
- human-review handling for uncertain cases;
- merge/unmerge and canonical-ID lifecycle.

Do not use the fictional customer data as real personal data.

---

## 10. Neo4j graph view

Neo4j is optional. The project contains a simplified adapter to illustrate a graph projection from RDF statements.

To use it:
1. Install/run Neo4j Community locally or use a permitted Neo4j instance.
2. Set `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` in `.env`.
3. Run the pipeline so the A-Box N-Quads file exists.
4. Run `python scripts/load_neo4j.py`.

The adapter is intentionally basic and is not a complete RDF-to-property-graph mapping. It does not implement RDF reasoning, named-graph fidelity, or SPARQL. For the AWS RDF target, use Neptune's supported RDF/SPARQL loading approach.

---

## 11. AWS deployment plan

The full checklist is in [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md). At a high level:

1. Choose AWS account and Region; review pricing and budgets.
2. Create a private S3 bucket with encryption, versioning, and access controls.
3. Create separate Lambda functions and least-privilege execution roles.
4. Package native/heavy dependencies for the Lambda runtime/architecture or use container images.
5. Configure Secrets Manager/SSM for credentials and API keys.
6. Configure VPC access for private Neptune/OpenSearch resources where required.
7. Add event triggers or Step Functions orchestration with retry and idempotency controls.
8. Configure logs, metrics, alarms, failure destinations, and data retention.
9. Test against non-sensitive sample data before any real source data.

The S3 code in `src/esa/storage.py` is commented as a reference. The Lambda handlers are thin scaffolds; event-specific S3 download, input validation, and production error handling still need implementation. Do not simply uncomment all cloud code and deploy without configuring it.

---

## 12. Lambda design

A practical separation of responsibilities:

| Lambda | Responsibility | Typical input/output |
|---|---|---|
| `ontology_ingest` | Fetch approved ontology export | Source → raw Turtle in S3 |
| `ontology_parse_validate` | Parse RDF; optionally validate; serialize | Turtle → N-Quads artifact |
| `structured_extract_map` | Read structured source and map fields | Source records → normalized records |
| `document_extract` | Extract text and metadata | Source documents → text/chunks |
| `embed_index` | Embed chunks and upsert vectors | Chunks → OpenSearch index |
| `entity_resolution` | Resolve verified IDs and flag candidates | Normalized records → ER output |
| `abox_rdf_build` | Build instance RDF | Mapped records → A-Box N-Quads |
| `graph_load` | Publish RDF to Neptune | RDF artifact → Neptune graph |
| `query_api` (optional) | Serve controlled query/retrieval endpoint | Request → answer/results |

Keep reusable logic in modules and keep handlers thin. Large documents, huge RDF graphs, or long-running transformations may need batch/container compute rather than a single Lambda. Ensure S3 output prefixes cannot recursively trigger the same function.

---

## 13. Configuration reference

| Variable | Purpose |
|---|---|
| `LOCAL_BUCKET_DIR` | Local S3-like storage root |
| `CENTREE_TTL_PATH` | Input ontology Turtle file |
| `SHACL_SHAPES_PATH` | SHACL shapes path (for integration) |
| `DOCS_DIR` | Document input folder |
| `STRUCTURED_DATA_PATH` | Sample structured JSON source |
| `RDF_OUTPUT_KEY` | Relative local object key for A-Box output |
| `EMBEDDING_PROVIDER` | `local` or `openai` |
| `LOCAL_EMBEDDING_MODEL` | SentenceTransformers model identifier |
| `OPENAI_API_KEY` | Secret API key; do not commit |
| `OPENAI_EMBEDDING_MODEL` | OpenAI embedding model name |
| `LLM_PROVIDER` | `none` or `openai` |
| `OPENAI_CHAT_MODEL` | OpenAI chat model name |
| `CHROMA_DIR` | Persistent Chroma directory |
| `CHROMA_COLLECTION` | Chroma collection name |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Optional Neo4j connection |
| `AWS_REGION`, `S3_BUCKET_NAME` | AWS configuration for deployment |

The `.env.example` file contains the starter values. The application loads `.env` through `python-dotenv`.

---

## 14. Troubleshooting

### `py` or `python` is not recognized
Install Python 3.11, enable PATH integration, and reopen CMD. Try `python --version` and `python -m pip --version`.

### Dependency installation fails
Upgrade pip first. Use a clean Python 3.11 virtual environment. Native packages may require compatible wheels and supported OS/architecture. For Lambda, build in a compatible Linux environment or use a container image.

### First document indexing is slow
The local SentenceTransformers model may be downloading. The first model load can take longer than later runs.

### OpenAI authentication error
Check that `.env` contains a valid `OPENAI_API_KEY`, that the key is active, and that the selected API models are available to your account. API calls may incur charges.

### Chroma collection is empty
Run `python scripts/index_documents.py`, verify `DOCS_DIR`, and check that supported files contain extractable text. Scanned PDFs need OCR.

### Neo4j connection fails
Confirm the database is running, Bolt URI/port are correct, and credentials match `.env`. Neo4j is optional for the main local pipeline.

### SHACL validation fails
Inspect the validation report and ensure your RDF conforms to the shapes. The included sample shape requires customer ID and full name for each target Customer.

---

## 15. Security, privacy, and cost

- Use fictional or approved non-sensitive data for testing.
- Never commit `.env`, API keys, credentials, or sensitive exports.
- Restrict S3 and database access using least privilege.
- Encrypt stored data and use TLS for network connections.
- Define retention, audit, and deletion procedures before processing enterprise data.
- Review whether documents may be sent to OpenAI or other external services.
- Local tools avoid managed-cloud service charges, but still use local compute/storage and may download model weights.
- OpenAI API usage is paid. AWS Lambda, S3, OpenSearch, Bedrock, Neptune, networking, and logging can incur charges. Verify current pricing and account eligibility before provisioning; do not assume a test is free.

---

## 16. Limitations and next steps

### Current scaffold limitations
- CENtree is represented by a fictional sample ontology; no live API integration is configured.
- Structured mapping is hard-coded for the sample JSON shape.
- SHACL validation is available as a helper but not wired into the default orchestration.
- Document ingestion supports basic text extraction; OCR, complex tables, and layout-aware parsing are not included.
- Local Chroma is not a distributed/managed production vector service.
- ER is illustrative and conservative; it is not a production master-data-management solution.
- Neo4j integration is a simplified graph projection, not Neptune equivalence.
- AWS adapters, IAM, triggers, monitoring, and production-grade retries need environment-specific work.

### Suggested implementation order
1. Replace sample ontology with an approved CENtree export and verify RDF parsing.
2. Agree on ontology versioning, namespace, graph naming, provenance, and mapping contracts.
3. Wire SHACL validation into A-Box publication and add negative test cases.
4. Implement structured-source extraction and field-to-ontology mapping.
5. Add document metadata, chunking policy, retrieval evaluation, and access controls.
6. Define ER rules, review workflow, and canonical ID policy.
7. Implement S3 adapters and separate Lambda handlers.
8. Configure OpenSearch/Bedrock or approved OpenAI integration with cost controls.
9. Load RDF into Neptune and test SPARQL queries and graph lifecycle.
10. Add automated tests, observability, deployment automation, and security review.
