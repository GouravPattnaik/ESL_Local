"""
graph_neo4j.py -- RDF N-Quads to Neo4j property-graph loader.

Architecture alignment:
  - Local stand-in for Amazon Neptune (RDF/SPARQL graph store).
  - NOT a Neptune equivalent: Neo4j uses Cypher, not SPARQL, and does not
    support named graphs, OWL reasoning, or SPARQL queries.
  - Useful for LOCAL visual exploration of the knowledge graph via Neo4j Browser.

Two loading strategies:
  1. load_nquads_typed()  -- RECOMMENDED: parses IRIs, assigns proper node labels
     (:Customer, :Account, :Loan, :Branch) and maps predicates to node properties.
     Produces a clean, browsable graph with named nodes.

  2. load_nquads_as_triples()  -- LEGACY: generic RDFTerm nodes with predicate as
     a relationship property. Preserves all triples but less readable in the browser.

Input : path to N-Quads file (.nq)
Output: count of statements loaded into Neo4j
"""

import logging
from pyoxigraph import Store, RdfFormat, NamedNode
from neo4j import GraphDatabase
from .config import setting

log = logging.getLogger(__name__)

EX = "https://example.org/esa/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# Maps ontology class IRIs to Neo4j node labels
CLASS_LABEL_MAP = {
    EX + "Customer": "Customer",
    EX + "Account":  "Account",
    EX + "Loan":     "Loan",
    EX + "Branch":   "Branch",
}

# Maps ontology property IRIs to Neo4j property names on the node
PROPERTY_MAP = {
    EX + "customerId":    "customerId",
    EX + "fullName":      "fullName",
    EX + "email":         "email",
    EX + "phone":         "phone",
    EX + "creditScore":   "creditScore",
    EX + "accountId":     "accountId",
    EX + "accountType":   "accountType",
    EX + "accountStatus": "accountStatus",
    EX + "balance":       "balance",
    EX + "loanId":        "loanId",
    EX + "loanType":      "loanType",
    EX + "loanAmount":    "loanAmount",
    EX + "loanStatus":    "loanStatus",
    EX + "interestRate":  "interestRate",
    EX + "branchId":      "branchId",
}

# Maps ontology object-property IRIs to Neo4j relationship types
RELATIONSHIP_MAP = {
    EX + "ownsAccount":  "OWNS_ACCOUNT",
    EX + "hasLoan":      "HAS_LOAN",
    EX + "registeredAt": "REGISTERED_AT",
    EX + "securedBy":    "SECURED_BY",
}


def _connect():
    """Create and return a Neo4j driver using settings from .env."""
    uri      = setting("NEO4J_URI",      "bolt://localhost:7687")
    user     = setting("NEO4J_USER",     "neo4j")
    password = setting("NEO4J_PASSWORD", "")
    database = setting("NEO4J_DATABASE", "neo4j")
    log.info("[Neo4j] Connecting to %s (db=%s, user=%s)", uri, database, user)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    log.info("[Neo4j] Connection verified OK")
    return driver, database


def load_nquads_typed(path: str) -> int:
    """
    RECOMMENDED: Load N-Quads into Neo4j with proper typed labels and properties.

    Strategy:
      Pass 1 -- Collect rdf:type assertions to map each IRI to its class label.
      Pass 2 -- Create typed nodes with properties.
      Pass 3 -- Create named relationships between nodes.

    Input : path to N-Quads file
    Output: total count of Cypher writes executed
    """
    log.info("[Neo4j][Typed] Loading N-Quads from: %s", path)
    store = Store()
    store.load(path=path, format=RdfFormat.N_QUADS)

    # --- Pass 1: classify subjects by rdf:type ---
    node_labels: dict[str, str] = {}   # IRI -> Neo4j label
    for q in store:
        if q.predicate.value == RDF_TYPE and isinstance(q.object, NamedNode):
            label = CLASS_LABEL_MAP.get(q.object.value)
            if label:
                node_labels[q.subject.value] = label

    log.info("[Neo4j][Typed] Pass 1: classified %d typed nodes: %s",
             len(node_labels),
             {v: sum(1 for x in node_labels.values() if x == v) for v in set(node_labels.values())})

    driver, database = _connect()
    count = 0

    with driver.session(database=database) as session:

        # --- Pass 2: create typed nodes with properties ---
        log.info("[Neo4j][Typed] Pass 2: creating typed nodes with properties ...")
        node_props: dict[str, dict] = {}
        for q in store:
            s = q.subject.value if hasattr(q.subject, "value") else str(q.subject)
            p = q.predicate.value
            if p == RDF_TYPE:
                continue
            prop_name = PROPERTY_MAP.get(p)
            if prop_name and s in node_labels:
                if s not in node_props:
                    node_props[s] = {}
                # Coerce numeric types
                val = str(q.object.value) if hasattr(q.object, "value") else str(q.object)
                node_props[s][prop_name] = _coerce(prop_name, val)

        for iri, label in node_labels.items():
            props = node_props.get(iri, {})
            props["iri"] = iri
            short = iri.split("/")[-1]  # e.g. "C001", "L003"
            props.setdefault("id", short)
            # Use MERGE on iri to be idempotent
            query = f"MERGE (n:{label} {{iri: $iri}}) SET n += $props"
            session.run(query, iri=iri, props=props)
            count += 1
            log.debug("[Neo4j][Typed]   MERGE (:%s {iri: %s})", label, short)

        log.info("[Neo4j][Typed] Pass 2 done: %d nodes merged", count)

        # --- Pass 3: create typed relationships ---
        log.info("[Neo4j][Typed] Pass 3: creating named relationships ...")
        rel_count = 0
        for q in store:
            s = q.subject.value if hasattr(q.subject, "value") else str(q.subject)
            p = q.predicate.value
            if not isinstance(q.object, NamedNode):
                continue
            o = q.object.value
            rel_type = RELATIONSHIP_MAP.get(p)
            if rel_type and s in node_labels and o in node_labels:
                query = (
                    "MATCH (a {iri: $s}) MATCH (b {iri: $o}) "
                    f"MERGE (a)-[r:{rel_type}]->(b)"
                )
                session.run(query, s=s, o=o)
                rel_count += 1
                count += 1
                log.debug("[Neo4j][Typed]   (%s)-[:%s]->(%s)",
                          s.split("/")[-1], rel_type, o.split("/")[-1])

        log.info("[Neo4j][Typed] Pass 3 done: %d relationships created", rel_count)

    driver.close()
    log.info("[Neo4j][Typed] Output: %d total Cypher writes. Graph ready for Cypher exploration.", count)
    return count


def _coerce(prop_name: str, val: str):
    """Coerce string values to int/float where appropriate."""
    int_props   = {"creditScore"}
    float_props = {"balance", "loanAmount", "interestRate"}
    if prop_name in int_props:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    if prop_name in float_props:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return val


def load_nquads_as_triples(path: str) -> int:
    """
    LEGACY: Load N-Quads as generic RDFTerm nodes.
    All subjects and objects become :RDFTerm nodes; predicates are stored as
    relationship properties. Less readable but preserves all triples verbatim.

    Kept for backward compatibility with scripts/load_neo4j.py.
    Prefer load_nquads_typed() for a browsable graph.

    Input : path to N-Quads file
    Output: count of statements loaded
    """
    log.info("[Neo4j][Legacy] Loading N-Quads (generic RDFTerm mode) from: %s", path)
    store = Store()
    store.load(path=path, format=RdfFormat.N_QUADS)

    driver, database = _connect()
    count = 0
    with driver.session(database=database) as session:
        for q in store:
            s, p, o = str(q.subject), str(q.predicate), str(q.object)
            session.run(
                "MERGE (s:RDFTerm {iri:$s}) "
                "MERGE (o:RDFTerm {value:$o}) "
                "MERGE (s)-[r:RDF_REL {predicate:$p}]->(o)",
                s=s, o=o, p=p,
            )
            count += 1
    driver.close()
    log.info("[Neo4j][Legacy] Output: %d RDF statements loaded.", count)
    return count
