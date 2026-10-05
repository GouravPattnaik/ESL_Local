import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.esa.vectorstore import search_chunks

# Get the query from the command line, or use a default
if len(sys.argv) > 1:
    query = " ".join(sys.argv[1:])
else:
    query = "what is the policy for buying a car?"

print(f"\n🔍 Running semantic search for: '{query}'\n")
results = search_chunks(query)

for i, r in enumerate(results, 1):
    print(f"[{i}] Source: {r['metadata']['source']} (Chunk {r['metadata']['chunk']})")
    print(f"    Text: {r['text'][:300]}...\n")