"""Check Graphiti layer status in Neo4j."""

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
    # Graphiti node counts
    print("=== GRAPHITI NODE COUNTS ===")
    result = s.run("""
        MATCH (n)
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
    """)
    total = 0
    for r in result:
        print(f"  {r['labels']}: {r['count']}")
        total += r['count']
    print(f"  GRAPHITI TOTAL: {total}")

    # Graphiti edge counts
    print()
    print("=== GRAPHITI EDGE COUNTS ===")
    result = s.run("""
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(r) AS count
        ORDER BY count DESC
    """)
    total_rels = 0
    for r in result:
        print(f"  {r['type']}: {r['count']}")
        total_rels += r['count']
    print(f"  GRAPHITI TOTAL EDGES: {total_rels}")

    # Episodes
    print()
    print("=== EPISODIC NODES (Graphiti episodes) ===")
    result = s.run("MATCH (e:Episodic) RETURN count(e) AS count")
    print(f"  Total episodes: {result.single()['count']}")

    result = s.run("""
        MATCH (e:Episodic)
        RETURN e.name AS name, e.group_id AS group_id, e.saga AS saga,
               e.created_at AS created_at, e.patient_id AS patient_id
        ORDER BY e.created_at
    """)
    for r in result:
        print(f"  [{r['saga']}] {r['name']}")
        print(f"    created: {r['created_at']}, patient: {r['patient_id']}")

    # Typed entities
    print()
    print("=== TYPED ENTITIES (Graphiti extracted) ===")
    result = s.run("""
        MATCH (e:Entity)
        RETURN labels(e) AS labels, e.name AS name, e.group_id AS group_id
        ORDER BY labels(e), e.name
    """)
    for r in result:
        print(f"  {r['labels']}: {r['name']}")

    # Facts (EntityEdge)
    print()
    print("=== FACTS / RELATIONSHIPS (EntityEdge) ===")
    result = s.run("""
        MATCH ()-[e:RELATES_TO]->()
        RETURN e.name AS edge_type, e.fact AS fact,
               e.valid_at AS valid_at, e.invalid_at AS invalid_at
        ORDER BY e.name, e.valid_at
    """)
    for r in result:
        invalid = f" -> invalid: {r['invalid_at']}" if r['invalid_at'] else ""
        print(f"  [{r['edge_type']}] {r['fact']}")
        print(f"    valid: {r['valid_at']}{invalid}")

    # Provenance links
    print()
    print("=== PROVENANCE LINKS ===")
    result = s.run("""
        MATCH (e:Episodic)-[:DERIVED_FROM]->(f:FHIRSource)
        RETURN e.name AS episode, f.fhir_key AS fhir_key, f.resource_type AS rtype
        ORDER BY e.name, f.fhir_key
        LIMIT 30
    """)
    for r in result:
        print(f"  {r['episode']}")
        print(f"    -> {r['rtype']}: {r['fhir_key']}")

    # Saga
    print()
    print("=== SAGA ===")
    result = s.run("MATCH (s:Saga) RETURN s.uuid AS uuid, s.group_id AS group_id, s.first_episode_uuid AS first, s.last_episode_uuid AS last")
    for r in result:
        print(f"  Saga: {r['uuid']}")
        print(f"    group: {r['group_id']}")
        print(f"    first: {r['first']}")
        print(f"    last: {r['last']}")

driver.close()
