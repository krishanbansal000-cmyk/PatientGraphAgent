"""Verify total node and relationship counts."""

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
    result = s.run("MATCH (n) RETURN count(n) AS total")
    nodes = result.single()["total"]
    print(f"Total nodes: {nodes}")

    result = s.run("MATCH ()-[r]->() RETURN count(r) AS total")
    rels = result.single()["total"]
    print(f"Total relationships: {rels}")

    print()
    print("=== ALL NODE LABELS ===")
    result = s.run("MATCH (n) RETURN labels(n) AS labels, count(n) AS count ORDER BY count DESC")
    for r in result:
        print(f"  {r['labels']}: {r['count']}")

    print()
    print("=== ALL RELATIONSHIP TYPES ===")
    result = s.run("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC")
    for r in result:
        print(f"  {r['type']}: {r['count']}")

driver.close()
