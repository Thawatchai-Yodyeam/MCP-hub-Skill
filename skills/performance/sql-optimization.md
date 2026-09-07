---
name: sql-optimization
category: performance
description: Database query optimization, EXPLAIN plan analysis, indexing strategies, and N+1 query prevention.
tags: [sql, database, performance, indexing, postgresql, mysql]
---

# SQL Query Optimization & Indexing

Best practices for inspecting and tuning SQL queries, diagnosing slow queries, and designing composite indexes.

## Checklist
- [ ] Run `EXPLAIN ANALYZE` (or database equivalent) to inspect query plans.
- [ ] Verify if queries trigger full table scans on large tables.
- [ ] Ensure composite indexes follow the Leftmost Prefix rule: Equality columns first, then Range columns.
- [ ] Avoid `SELECT *`. Select only the required columns to reduce I/O and network payload.
- [ ] Detect and eliminate ORM N+1 query patterns (use `JOIN FETCH`, `select_related`, or batching).
- [ ] Avoid applying functions to indexed columns in WHERE clauses (e.g., `WHERE DATE(created_at) = ...` prevents index usage).

## Anti-patterns
- Using leading wildcard in `LIKE '%text%'` which disables standard B-Tree indexes (use Full-Text Search or trigram indexes instead).
- Over-indexing: Creating indexes for every single column, severely hurting write/insert throughput.
