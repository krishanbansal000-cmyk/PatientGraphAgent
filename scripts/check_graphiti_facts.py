"""Check Graphiti RELATES_TO facts and entity relationships."""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.environ.get("NEO4J_URI", "")
USER = os.environ.get("NEO4J_USERNAME", "")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

if not URI or not USER or not PASSWORD:
    raise RuntimeError("Set NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD before running")

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

with driver.session(database=DATABASE) as s:
    # RELATES_TO edges with fact text
    print("=== RELATES_TO FACTS (semantic relationships) ===")
    result = s.run("""
        MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
        RETURN labels(a) AS src_labels, a.name AS src_name,
               type(r) AS rel_type, r.fact AS fact, r.name AS edge_name,
               labels(b) AS tgt_labels, b.name AS tgt_name,
               r.valid_at AS valid_at, r.invalid_at AS invalid_at,
               r.created_at AS created_at
        ORDER BY r.valid_at, a.name
    """)
    count = 0
    for r in result:
        count += 1
        src_type = [l for l in r['src_labels'] if l != 'Entity']
        tgt_type = [l for l in r['tgt_labels'] if l != 'Entity']
        invalid = f" [invalid: {r['invalid_at']}]" if r['invalid_at'] else ""
        print(f"\n  [{r['edge_name'] or r['rel_type']}]")
        print(f"    {src_type} '{r['src_name']}' -> {tgt_type} '{r['tgt_name']}'")
        print(f"    Fact: {r['fact']}")
        print(f"    Valid: {r['valid_at']}{invalid}")
    print(f"\n  TOTAL RELATES_TO EDGES: {count}")

    # MENTIONS edges (episode -> entity)
    print()
    print("=== MENTIONS (episode -> entity) sample ===")
    result = s.run("""
        MATCH (e:Episodic)-[:MENTIONS]->(ent:Entity)
        RETURN e.name AS episode, labels(ent) AS ent_labels, ent.name AS ent_name
        ORDER BY e.name, ent.name
        LIMIT 30
    """)
    for r in result:
        ent_type = [l for l in r['ent_labels'] if l != 'Entity']
        print(f"  {r['episode'][:60]}")
        print(f"    -> {ent_type}: {r['ent_name']}")

    # NEXT_EPISODE chain
    print()
    print("=== NEXT_EPISODE CHAIN ===")
    result = s.run("""
        MATCH (e1:Episodic)-[:NEXT_EPISODE]->(e2:Episodic)
        RETURN e1.name AS from_ep, e2.name AS to_ep
        ORDER BY e1.name
    """)
    for r in result:
        print(f"  {r['from_ep'][:50]}")
        print(f"    -> NEXT -> {r['to_ep'][:50]}")

driver.close()
