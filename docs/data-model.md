# Data Model

Schema version 3 contains `datasets`, `samples`, `media_assets`, `events`, `persons`, `evidence`, `model_predictions`, and `annotation_revisions`. A sample is identified by a stable ID and source hash; child event/person IDs are deterministic during CSV migration.

Data lineage is: source media -> SHA-256 and metadata -> sample row -> human or model events/persons -> optional evidence/provenance -> quality report -> CSV/JSONL export. `person_count` is derived from `persons`; legacy `person_tag_list` is discarded on import and never emitted.

Events and people carry source, confidence, review status, annotator, and revision fields. Unknown CSV columns are preserved in `samples.extra_json`. Reviewed rows with invalid structured JSON are rejected rather than silently converted.

SQLite is the source of truth for browser saves. `person_identity_attributes`
is serialized as a JSON array, and an empty array is valid for zero-person
samples. The CSV adapter remains available for import/export compatibility and
never emits the legacy `person_tag_list` field.
