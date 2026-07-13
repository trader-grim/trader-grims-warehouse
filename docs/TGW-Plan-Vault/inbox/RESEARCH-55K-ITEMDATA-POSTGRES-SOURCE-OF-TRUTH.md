<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# I have a dataset consisting of 55k+ items each with a json file and some photos. My app uses those for the data set. Please compare advantages and disadvantages of moving the source of truth dataset to a postgresql database.

Yes — moving your source-of-truth dataset from per-item JSON files into PostgreSQL can give you much stronger queryability, concurrency control, and transactional integrity, but it also introduces operational complexity and changes the failure modes you deal with day to day. For a dataset shaped like yours — 55k+ items, JSON-rich records, photos on disk, queue workers, and evolving automation — the trade is usually less “DB or files” and more “which parts become authoritative in DB, and which stay file-backed.”[^1_1][^1_2]

## What PostgreSQL improves

A PostgreSQL source of truth gives you atomic updates, constraints, row-level locking, indexes, and multi-writer safety that plain JSON files do not provide. That matters when several workers, editors, importers, AI processes, and marketplace sync jobs can touch the same item at different times.[^1_1][^1_3]

It also makes searching and reporting much better: PostgreSQL supports `jsonb`, JSON operators, JSON path queries, and indexing for semi-structured documents, so you can keep flexible item payloads while still querying them efficiently. PostgreSQL also supports declarative partitioning if some tables grow large enough to need it later.[^1_4][^1_2][^1_5]

## Main advantages

- **Concurrency safety:** transactions and row locks prevent two workers from clobbering the same item update at once, which is a major upgrade over file-level coordination and rename/lock conventions.[^1_1]
- **Better querying:** ad hoc queries across 55k+ items become normal SQL instead of filesystem crawls plus JSON parsing, especially for cross-item reports, dedupe, quality audits, and “find all items missing X but having Y.”[^1_2][^1_4]
- **Integrity rules:** you can enforce uniqueness, foreign keys, check constraints, and state transitions in the database instead of trusting every app path to behave perfectly.[^1_3][^1_1]
- **Operational visibility:** queues, edits, sync states, retries, and audit history fit naturally into tables and are easier to inspect than scattered files.[^1_6][^1_1]
- **Schema evolution with flexibility:** a hybrid relational-plus-`jsonb` model lets you normalize hot fields while still preserving raw or evolving structures.[^1_3][^1_2]


## Main disadvantages

- **Higher ops burden:** backups, restores, replication, vacuum, bloat control, migrations, and DB performance tuning become mission-critical because the database is now the heart of the system.[^1_5][^1_2]
- **Harder manual inspection:** JSON files are easy to browse, diff, rsync, grep, and repair with shell tools; a DB makes casual filesystem-level inspection less direct. That is especially relevant for your Linux-heavy, operator-friendly workflow.
- **Migration complexity:** moving from per-item files to DB truth means deciding how history, sidecars, photos, AI outputs, and partial failures map into transactions without losing the simplicity you already have.[^1_7]
- **Different corruption/blast radius profile:** a bad app bug against a database can damage many rows quickly, whereas file-oriented systems often fail more locally. Good migrations, permissions, and append-only history reduce this, but the risk is real.[^1_1][^1_3]
- **Photo storage is awkward in-DB:** PostgreSQL can store large objects, but that adds lifecycle management concerns, and even PostgreSQL’s docs note orphan risks and trigger-based cleanup requirements for large objects. For your case, photos usually fit better on disk or object storage with the DB storing metadata and paths.[^1_8][^1_9]


## Best fit for your dataset

For TGW-like data, the strongest design is usually **PostgreSQL for item truth plus filesystem/object storage for photos**, not “put everything into the database.” Your current architecture already leans this way conceptually: queue/state logic belongs in PostgreSQL, while large image assets and some derived artifacts remain file-backed.[^1_1]

A practical shape would be:

- Core item table for identity, status, location, timestamps, and frequently filtered fields.[^1_3]
- `jsonb` column for flexible item document content, including nested marketplace or AI-derived structures when they are still evolving.[^1_4][^1_2]
- Separate history/audit tables for immutable change tracking instead of rewriting one giant document forever.[^1_1]
- Photo table with item FK, filenames, hashes, metadata, derivatives, and storage path/URI, while actual image bytes stay outside PostgreSQL.[^1_9][^1_7]


## Likely recommendation

Given your workload and architecture style, I would not recommend a full “database stores every byte and replaces the filesystem entirely” move. I would recommend promoting PostgreSQL to the source of truth for item metadata, workflow state, search/filter fields, and audit history, while keeping photos and possibly some heavyweight derived blobs outside the DB and referenced by path or content hash.[^1_2][^1_9][^1_1][^1_3]

That gives you the biggest gains — consistency, query power, worker safety, and reporting — without losing the operational strengths of file-backed media. The main question is not whether PostgreSQL is better in the abstract, but whether you want a **full replacement** or a **hybrid authority model**; for your system, the hybrid model is usually the safer and more durable answer.[^1_7][^1_6]

Would you like a concrete target schema for a hybrid design: `items`, `item_history`, `item_media`, `item_analysis`, and `item_events`?
<span style="display:none">[^1_10][^1_11][^1_12][^1_13][^1_14][^1_15][^1_16][^1_17][^1_18][^1_19]</span>

<div align="center">⁂</div>

[^1_1]: https://www.perplexity.ai/search/22a393b8-bb85-400f-9645-635cd24019fc

[^1_2]: https://www.postgresql.org/docs/current/datatype-json.html

[^1_3]: https://www.perplexity.ai/search/077524ed-5a89-4dfa-a4b7-0b6d6f3b3196

[^1_4]: https://www.postgresql.org/docs/current/functions-json.html

[^1_5]: https://www.postgresql.org/docs/current/ddl-partitioning.html

[^1_6]: https://www.perplexity.ai/search/503ab13e-44d7-4939-a069-9fe142f9a530

[^1_7]: https://www.perplexity.ai/search/9a8830b1-df9b-41a9-aec1-cbddf7a81396

[^1_8]: https://www.postgresql.org/docs/current/largeobjects.html

[^1_9]: https://www.postgresql.org/docs/current/lo.html

[^1_10]: https://docs.postgresql.fr/17/largeobjects.html

[^1_11]: https://www.postgresql.org/docs/9.4/functions-json.html

[^1_12]: https://www.postgresql.org/docs/7.1/jdbc-lo.html

[^1_13]: https://docs.postgresql.fr/16/functions-json.html

[^1_14]: https://www.postgresql.org/docs/current/ecpg-lo.html

[^1_15]: https://docs.postgresql.fr/18/datatype-json.html

[^1_16]: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_Partitions.html

[^1_17]: https://www.postgresql.org/docs/current/catalog-pg-partitioned-table.html

[^1_18]: https://stackoverflow.com/questions/22654170/explanation-of-jsonb-introduced-by-postgresql

[^1_19]: https://www.dbvis.com/thetable/everything-you-need-to-know-about-the-postgres-jsonb-data-type/


---

# if I read this right, given the simple set of on json one set of photos per folder moving the source of truth for the json to the db and maybe an occasional output to json for the archive, photos stay put?

Yes — that’s the cleanest reading of it. If each folder already has one JSON record plus one photo set, then moving the JSON truth into PostgreSQL and keeping photos where they are is the sensible default; you can still emit JSON occasionally for archive/export without making the files the primary system of record.[^2_1][^2_2]

## What stays put

The photos can stay exactly where they are on disk, with the database storing the item record, paths, hashes, metadata, and any derived fields needed for search or workflow. That keeps the heavy binary data in the filesystem, which is usually the better home for media, while PostgreSQL holds the authoritative structured state.[^2_3][^2_1]

## What moves

What moves is the current JSON content: item identity, listing fields, status, timestamps, provenance, and any flexible fields you want to query across all items. PostgreSQL can hold that as normalized columns plus `jsonb` for the parts that are still evolving, so you do not have to flatten everything on day one.[^2_2][^2_4][^2_5]

## Archive JSON output

Occasional JSON export still makes sense as an archive, interchange format, or disaster-recovery snapshot. In practice, that gives you the best of both worlds: database-first operations, but a file artifact you can inspect, diff, or restore from if needed.[^2_2]

## Practical rule

So the rule of thumb is: **database for truth, filesystem for photos, JSON only as a generated artifact**. For your setup, that is the least disruptive path and it aligns well with the way you already separate item data from media and queue/state processing.[^2_6]

Would you like a concrete folder-to-database mapping, including which fields stay in PostgreSQL and which fields remain derived/export-only?
<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://teamtreehouse.com/community/somebody-please-help-me-put-images-in-json

[^2_2]: https://www.postgresql.org/docs/current/datatype-json.html

[^2_3]: https://www.postgresql.org/docs/current/lo.html

[^2_4]: https://www.postgresql.org/docs/current/functions-json.html

[^2_5]: https://www.perplexity.ai/search/077524ed-5a89-4dfa-a4b7-0b6d6f3b3196

[^2_6]: https://www.perplexity.ai/search/22a393b8-bb85-400f-9645-635cd24019fc

[^2_7]: https://github.com/thumbsup/exiftool-json-db

[^2_8]: https://www.reddit.com/r/googlephotos/comments/1bp8mm9/what_to_do_with_the_json_files/

[^2_9]: https://support.google.com/photos/thread/1731165/what-do-i-lose-if-i-disregard-json-files?hl=en

[^2_10]: https://github.com/aaronkollasch/photomanager

[^2_11]: https://metavaultstudioapp.com/en/google-takeout-json-metadata

[^2_12]: https://github.com/fredrikburmester/photos-to-json

[^2_13]: https://metadatafixer.com/learn/google-takeout-json-files-explained

[^2_14]: https://www.facebook.com/groups/Banburyshireinfo/posts/10161451432796622/

[^2_15]: https://discourse.gohugo.io/t/image-gallery-using-data-from-json-files/44507


---

# good . That was my design too. I really don't like my photos in a database, and they aren't hit as hard ad the data.

Exactly — that matches a sane, low-friction design. Keep photos on disk, keep the database focused on item truth and workflow, and treat the DB as the thing you query and mutate most often while the images remain a slower-moving asset layer.[^3_1][^3_2]

That separation is especially good for your use case because photos are not hit as hard as the item data, and PostgreSQL is best when it is not carrying big binary blobs around with every backup, vacuum, or restore. PostgreSQL’s own large-object docs exist, but the common guidance is still to keep files external and store paths or metadata in the database unless you have a very specific reason not to.[^3_3][^3_4][^3_5][^3_6][^3_1]

So your design becomes:

- PostgreSQL: canonical JSON/item state, searchable fields, history, queue/workflow state, and photo metadata.[^3_7][^3_8][^3_9]
- Filesystem: the actual photos, unchanged in place, referenced by relative path or item/file identifiers.[^3_2][^3_5]
- JSON export: occasional archive or interchange output generated from the DB when you want a portable snapshot.[^3_9]

That is a solid architecture for TGW: database for operational truth, filesystem for media, and export JSON as a byproduct rather than the primary store.
<span style="display:none">[^3_10][^3_11][^3_12][^3_13][^3_14][^3_15][^3_16][^3_17][^3_18][^3_19]</span>

<div align="center">⁂</div>

[^3_1]: https://www.prisma.io/docs/postgres/query-optimization/recommendations/storing-blob-in-database

[^3_2]: https://stackoverflow.com/questions/74263835/saving-product-images-and-translation-in-jsonb-column-or-another-table

[^3_3]: https://www.postgresql.org/docs/current/largeobjects.html

[^3_4]: https://stackoverflow.com/questions/9605922/are-there-performance-issues-storing-files-in-postgresql

[^3_5]: https://www.reddit.com/r/PostgreSQL/comments/qcw9e5/should_i_store_content_imagesvideos_etc_in_a/

[^3_6]: https://www.postgresql.org/docs/current/lo.html

[^3_7]: https://www.perplexity.ai/search/22a393b8-bb85-400f-9645-635cd24019fc

[^3_8]: https://www.perplexity.ai/search/077524ed-5a89-4dfa-a4b7-0b6d6f3b3196

[^3_9]: https://www.postgresql.org/docs/current/datatype-json.html

[^3_10]: https://news.ycombinator.com/item?id=28678359

[^3_11]: https://www.slingacademy.com/article/postgresql-how-to-store-images-in-database-and-why-you-shouldnt/

[^3_12]: https://www.reddit.com/r/PostgreSQL/comments/1k4ecqg/should_i_use_postgre_sql_for_images_or_not/

[^3_13]: https://www.tigerdata.com/learn/handling-large-objects-in-postgres

[^3_14]: https://news.ycombinator.com/item?id=37325379

[^3_15]: https://www.postgresql.org/docs/7.0/largeobjects.htm

[^3_16]: https://dev.to/tekhunt/dealing-with-large-objects-in-postgresql-47fd

[^3_17]: https://www.postgresql.org/docs/7.4/jdbc-binary-data.html

[^3_18]: https://www.postgresql.org/message-id/Xns943C5778D7F72rr8xca@200.46.204.72

[^3_19]: https://wiki.postgresql.org/wiki/BinaryFilesInDB

