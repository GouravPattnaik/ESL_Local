from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from esa.graph_neo4j import load_nquads_as_triples
path = "local_bucket/knowledge/abox/instances.nq"
print(f"Loaded {load_nquads_as_triples(path)} RDF statements into Neo4j graph view")
