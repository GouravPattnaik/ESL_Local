# ESA Pipeline — Cheat Sheet

**Enterprise Semantic Analytics (ESA)** · Local POC → AWS Architecture

---

## Technology Stack

| Component | Local (POC) | AWS (Target) |
|-----------|------------|--------------|
| **Ontology store** | PyOxigraph (in-memory RDF store) | Amazon Neptune (RDF/SPARQL) |
| **Ontology language** | OWL 2 / Turtle (.ttl) from CENtree | Same |
| **Graph validation** | pySHACL | Same (Lambda) |
| **Graph DB** | Neo4j Community (bolt://localhost:7687) | Amazon Neptune |
| **Graph query** | Cypher (Neo4j Browser) | SPARQL (Neptune endpoint) |
| **Embedding model** | `sentence-transformers/all-MiniLM-L6-v2` (free, local) | Amazon Bedrock Titan / OpenAI `text-embedding-3-small` |
| **Vector store** | ChromaDB (local_bucket/vectorstore/chroma) | Amazon OpenSearch Serverless |
| **LLM (RAG answers)** | None configured (`LLM_PROVIDER=none`) | OpenAI `gpt-4o-mini` / Amazon Bedrock Claude |
| **Entity resolution** | RapidFuzz (fuzzy string matching) | AWS Entity Resolution / ML service |
| **Data format** | N-Quads (.nq) | N-Quads (.nq) via S3 → Neptune bulk load |
| **Orchestrator** | `run_pipeline.py` (subprocess) | AWS Step Functions |
| **Source data** | `data/records/*.json` (local JSON) | Databricks / Glue Data Catalogue |
| **Business glossary** | `SEMANTIC_MAPPING_TABLE` (hard-coded) | Collibra API + LLM suggestions |
| **Document source** | `data/documents/` (local files) | Amazon S3 / SharePoint |
| **AWS region** | — | `ap-south-1` (Mumbai) |

---

## Steps Cheat Sheet

Run from the project root `d:\Semantic_Layer` with venv active.

```powershell
# Activate venv first
venv\Scripts\activate
```

---

### Step 1 — Ontology / T-Box Ingestion

```powershell
python scripts/step1_ontology_tbox.py
```

| | |
|--|--|
| **What** | Parses CENtree Turtle ontology → N-Quads |
| **Input** | `data/centree/ontology.ttl` |
| **Output** | `local_bucket/ontology/centree/ontology.nq` (86 triples) |
| **When to re-run** | Any time the CENtree ontology is updated |
| **Optional flag** | `--validate-shacl` (requires `pip install pyshacl`) |

---

### Step 2 — Metadata Normalisation

```powershell
python scripts/step2_metadata_normalisation.py
```

| | |
|--|--|
| **What** | Standardises raw field names (synonym map), coerces types, validates required fields |
| **Input** | `data/records/customers.json`, `accounts.json`, `loans.json` |
| **Output** | `local_bucket/normalised/customers.json`, `accounts.json`, `loans.json` |
| **Simulates** | Collibra business glossary synonym resolution |
| **When to re-run** | Any time source data changes |

---

### Step 3 — Semantic Mapping

```powershell
python scripts/step3_semantic_mapping.py
```

| | |
|--|--|
| **What** | Maps each normalised field → CENtree ontology IRI + XSD datatype |
| **Input** | `local_bucket/normalised/*.json` (Step 2 output) |
| **Output** | `local_bucket/semantic_mapping/approved_mappings.json` |
| **Simulates** | Lambda (LLM + Collibra) field-to-IRI suggestion + human approval |
| **Watch for** | `UNMAPPED` fields logged as WARNING → add to `SEMANTIC_MAPPING_TABLE` in the script |
| **When to re-run** | After Step 2, or if new fields are added to source data |

---

### Step 4 — Entity Resolution

```powershell
python scripts/step4_entity_resolution.py

# Lower threshold to catch more fuzzy matches:
python scripts/step4_entity_resolution.py --threshold 90
```

| | |
|--|--|
| **What** | Finds duplicate customers across source systems (strong ID + fuzzy name match) |
| **Input** | `local_bucket/normalised/customers.json` |
| **Output** | `local_bucket/knowledge/entity_resolution/results.json` |
| **Algorithm** | RapidFuzz `token_sort_ratio`, default threshold 96% |
| **Known match** | `Gourav Pattnaik (C001) ↔ Gourav Patnaik (C005)` — flagged, needs human review |
| **NEVER auto-merges** | Review candidates only flagged, not merged automatically |
| **When to re-run** | After Step 2, or when new customer records are added |

---

### Step 5 — A-Box RDF Generation + SHACL Validation

```powershell
python scripts/step5_abox_rdf_generation.py

# Skip SHACL if pyshacl not installed:
python scripts/step5_abox_rdf_generation.py --skip-shacl
```

| | |
|--|--|
| **What** | Builds RDF instance triples for all entities using approved semantic mappings |
| **Input** | `local_bucket/normalised/*.json` + `local_bucket/semantic_mapping/approved_mappings.json` |
| **Output** | `local_bucket/knowledge/abox/instances.nq` (228 triples) |
| **Graph built** | Customer → Account (OWNS_ACCOUNT) · Customer → Loan (HAS_LOAN) · Customer → Branch (REGISTERED_AT) · Loan → Account (SECURED_BY) |
| **RDF library** | PyOxigraph |
| **Validation** | pySHACL against `data/shapes/ontology_shapes.ttl` |
| **When to re-run** | After Step 2 or Step 3 |

---

### Step 6 — Document Processing + Vector Store Indexing

```powershell
python scripts/step6_document_processing.py

# Custom verification query:
python scripts/step6_document_processing.py --verify "minimum credit score"
```

| | |
|--|--|
| **What** | Extracts text from documents, chunks it, embeds it, stores in ChromaDB |
| **Input** | `data/documents/` (.txt, .md, .pdf, .docx) |
| **Output** | `local_bucket/vectorstore/chroma/` (persistent ChromaDB) |
| **Embedding model** | `all-MiniLM-L6-v2` via SentenceTransformers (local, free) |
| **Vector store** | ChromaDB, collection = `esa_documents` |
| **Chunk size** | 900 chars, 120-char overlap |
| **First run** | Downloads ~90MB model once; subsequent runs are fast |
| **When to re-run** | Any time documents in `data/documents/` are added or changed |

---

### Step 7 — Graph Load (RDF → Neo4j)

```powershell
python scripts/step7_graph_load.py

# No Neo4j? Use dry-run to see what would be loaded:
python scripts/step7_graph_load.py --dry-run
```

| | |
|--|--|
| **What** | Loads A-Box N-Quads into Neo4j as typed, named nodes and relationships |
| **Input** | `local_bucket/knowledge/abox/instances.nq` (228 triples) |
| **Graph DB** | Neo4j Community at `bolt://localhost:7687` |
| **Node types** | `:Customer`, `:Account`, `:Loan`, `:Branch` |
| **Relationships** | `-[:HAS_LOAN]->`, `-[:OWNS_ACCOUNT]->`, `-[:REGISTERED_AT]->`, `-[:SECURED_BY]->` |
| **AWS equivalent** | Neptune bulk load from S3 (N-Quads format) |
| **Explore** | Neo4j Browser → http://localhost:7474 |
| **When to re-run** | After Step 5, or to refresh the graph after data updates |

**Quick Cypher queries for Neo4j Browser:**

```cypher
// Count all node types
MATCH (n) RETURN labels(n)[0] AS type, count(n) AS total ORDER BY total DESC;

// Customer full profile
MATCH (c:Customer {id: 'C001'})
OPTIONAL MATCH (c)-[:OWNS_ACCOUNT]->(a) OPTIONAL MATCH (c)-[:HAS_LOAN]->(l)
RETURN c.fullName, c.creditScore, collect(a.accountType), collect(l.loanType);

// Risky customers (credit score < 700)
MATCH (c:Customer)-[:HAS_LOAN]->(l:Loan)
WHERE c.creditScore < 700
RETURN c.fullName, c.creditScore, l.loanType, l.loanAmount ORDER BY c.creditScore;

// Full graph visualisation
MATCH path = (c:Customer)-[*1..2]->() RETURN path LIMIT 50;
```

---

### Step 8 — Query / RAG

```powershell
python scripts/step8_query_rag.py "What is the loan eligibility criteria?"

# More results:
python scripts/step8_query_rag.py --top-k 5 "What happens if a customer defaults?"
```

| | |
|--|--|
| **What** | Semantic search over indexed documents; optional LLM answer generation |
| **Input** | Natural language question + ChromaDB (Step 6 output) |
| **Embedding** | Same model as Step 6: `all-MiniLM-L6-v2` |
| **Vector store** | ChromaDB `esa_documents` collection |
| **LLM** | Currently `none` — set `LLM_PROVIDER=openai` in `.env` for GPT answers |
| **LLM model** | `gpt-4o-mini` (when enabled) |
| **AWS equivalent** | OpenSearch (retrieval) → Bedrock Claude (answer) |
| **When to use** | Any time — query the pipeline interactively |

---

## Run Everything at Once

```powershell
# All steps 1-6 (Neo4j optional, skipped here)
python scripts/run_pipeline.py --skip-graph-load

# All steps including Neo4j load
python scripts/run_pipeline.py

# Re-run specific steps only (e.g. after data change)
python scripts/run_pipeline.py --steps 2 3 4 5 7

# With SHACL validation
python scripts/run_pipeline.py --skip-graph-load --validate-shacl
```

---

## Config — Key `.env` Settings

```ini
# Embedding: local (free) or openai (paid)
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
OPENAI_API_KEY=                          # fill in for OpenAI embeddings

# LLM for RAG answers: none or openai
LLM_PROVIDER=none
OPENAI_CHAT_MODEL=gpt-4o-mini

# Neo4j (local graph DB, optional)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password_here

# Data paths (defaults are fine for local POC)
CENTREE_TTL_PATH=data/centree/ontology.ttl
STRUCTURED_DATA_PATH=data/records
DOCS_DIR=data/documents
LOCAL_BUCKET_DIR=local_bucket
```

---

## Output File Map

```
local_bucket/
  ontology/centree/ontology.nq              ← Step 1  (86 triples, T-Box)
  normalised/
    customers.json                           ← Step 2  (12 records)
    accounts.json                            ← Step 2  (12 records)
    loans.json                               ← Step 2  (8 records)
  semantic_mapping/approved_mappings.json    ← Step 3  (14 fields mapped)
  knowledge/
    entity_resolution/results.json           ← Step 4  (12 entities, 1 review candidate)
    abox/instances.nq                        ← Step 5  (228 triples, A-Box)
  vectorstore/chroma/                        ← Step 6  (ChromaDB embeddings)

logs/
  step1_ontology_YYYYMMDD_HHMMSS.log
  step2_metadata_norm_YYYYMMDD_HHMMSS.log
  step3_semantic_mapping_YYYYMMDD_HHMMSS.log
  step4_entity_resolution_YYYYMMDD_HHMMSS.log
  step5_abox_rdf_YYYYMMDD_HHMMSS.log
  step6_document_processing_YYYYMMDD_HHMMSS.log
  step7_graph_load_YYYYMMDD_HHMMSS.log
  step8_query_rag_YYYYMMDD_HHMMSS.log
  run_pipeline_YYYYMMDD_HHMMSS.log
```

---

## Known Issues / Watch Points

| Issue | Fix |
|-------|-----|
| `currency` field is UNMAPPED in Step 3 | Add `currency` to `SEMANTIC_MAPPING_TABLE` in `step3_semantic_mapping.py` |
| `Gourav Pattnaik ↔ Gourav Patnaik` (C001/C005) | Review `results.json` — may be the same person |
| Neo4j not running | Use `--dry-run` or `--skip-graph-load`; N-Quads file is still the canonical output |
| Step 6 slow on first run | SentenceTransformer model downloads ~90MB once; subsequent runs are fast |
| LLM answers not working | Set `LLM_PROVIDER=openai` and fill `OPENAI_API_KEY` in `.env` |
| SHACL fails | A Customer is missing `customerId` or `fullName` — check Step 2 normalised output |

---

## What Has Been Done vs What Remains

| Component | Status | Notes |
|-----------|--------|-------|
| T-Box ontology parse | ✅ Done | 86 triples from CENtree |
| Metadata normalisation | ✅ Done | 32 records across 3 entities |
| Semantic mapping | ✅ Done | 14/15 fields mapped (currency needs review) |
| Entity resolution | ✅ Done | 1 fuzzy candidate flagged for human review |
| A-Box RDF generation | ✅ Done | 228 triples, N-Quads output |
| SHACL validation | ✅ Done | Wired in Step 5 (`--skip-shacl` flag to bypass) |
| Document indexing | ✅ Done | ChromaDB with local SentenceTransformer |
| Neo4j graph load | ✅ Done | Fix applied; typed nodes + named relationships |
| RAG query interface | ✅ Done | Retrieval only; enable OpenAI for LLM answers |
| LLM-based semantic mapping | 🔲 Not done | Currently rule-based table; needs LLM integration |
| `currency` field mapping | 🔲 Pending | Add to `SEMANTIC_MAPPING_TABLE` |
| `Gourav Pattnaik` ER review | 🔲 Pending | Human needs to confirm if C001 = C005 |
| AWS S3 integration | 🔲 Not done | Hooks exist in `src/esa/storage.py`, needs config |
| AWS Neptune load | 🔲 Not done | N-Quads file is ready; needs Neptune cluster |
| AWS OpenSearch | 🔲 Not done | Replace ChromaDB calls in `vectorstore.py` |
| AWS Bedrock / Lambda packaging | 🔲 Not done | Each script → Lambda function container |
